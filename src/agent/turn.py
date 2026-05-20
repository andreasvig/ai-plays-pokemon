"""Turn manager: orchestrates the agent turn loop."""

import asyncio
import json
import logging
import sys
import time
from typing import Any, Optional

from PIL import Image
from pydantic_ai.messages import (
    ImageUrl,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    UserContent,
    UserPromptPart,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    RetryPromptPart,
)
from pydantic_ai.models.openai import OpenAIModel

from src.agent.agent import AgentDeps, GameAction, create_agent
from src.emulator import EmulatorClient, VisionPipeline, OCRRunner
from src.core import RunLogger, StateManager

logger = logging.getLogger(__name__)

# Markers that suggest a model rejected thinking/reasoning params
_THINKING_ERROR_MARKERS = (
    "reasoning", "thinking", "not supported", "unsupported parameter",
)


def _serialize_messages(messages) -> list[dict]:
    """Convert Pydantic AI messages to serializable dicts for logging."""
    trace = []
    for msg in messages:
        if isinstance(msg, ModelRequest):
            for part in msg.parts:
                if isinstance(part, SystemPromptPart):
                    trace.append({"role": "system", "content": part.content})
                elif isinstance(part, UserPromptPart):
                    if isinstance(part.content, str):
                        content = part.content
                    elif isinstance(part.content, (list, tuple)):
                        # Multimodal content — extract text parts, replace images with placeholder
                        text_bits = []
                        for item in part.content:
                            if isinstance(item, str):
                                text_bits.append(item)
                            elif isinstance(item, ImageUrl):
                                text_bits.append("[image]")
                            else:
                                text_bits.append(f"[{type(item).__name__}]")
                        content = "\n".join(text_bits)
                    else:
                        content = str(part.content)
                    trace.append({"role": "user", "content": content})
                elif isinstance(part, ToolReturnPart):
                    trace.append({
                        "role": "tool_result",
                        "tool_name": part.tool_name,
                        "content": str(part.content)[:2000],
                    })
                elif isinstance(part, RetryPromptPart):
                    trace.append({
                        "role": "retry",
                        "content": str(part.content)[:1000],
                    })
                else:
                    trace.append({"role": "request_part", "type": type(part).__name__, "content": str(part)[:500]})
        elif isinstance(msg, ModelResponse):
            _extract_openrouter_reasoning(msg, trace)
            for part in msg.parts:
                if isinstance(part, TextPart):
                    # Prompted-output mode emits the GameAction JSON as a TextPart
                    # rather than a final_result tool call. Re-route to the same
                    # trace shape so terminal display formats it identically.
                    parsed_action = _try_parse_game_action(part.content)
                    if parsed_action is not None:
                        trace.append({
                            "role": "tool_call",
                            "tool_name": "final_result",
                            "args": parsed_action,
                        })
                    else:
                        trace.append({"role": "assistant", "content": part.content})
                elif isinstance(part, ThinkingPart):
                    trace.append({"role": "thinking", "content": part.content})
                elif isinstance(part, ToolCallPart):
                    trace.append({
                        "role": "tool_call",
                        "tool_name": part.tool_name,
                        "args": part.args if isinstance(part.args, dict) else str(part.args),
                    })
                else:
                    trace.append({"role": "response_part", "type": type(part).__name__, "content": str(part)[:500]})
        else:
            trace.append({"role": "unknown", "type": type(msg).__name__, "content": str(msg)[:500]})
    return trace


def _extract_openrouter_reasoning(msg: ModelResponse, trace: list[dict]) -> None:
    """Extract reasoning from OpenRouter's response if present."""
    try:
        if msg.provider_details:
            reasoning = msg.provider_details.get('reasoning')
            if reasoning:
                trace.append({"role": "thinking", "content": reasoning})
    except Exception:
        pass


def _extract_cost_from_messages(messages) -> float:
    """Extract total USD cost from OpenRouter provider_details.

    OpenRouter includes actual cost in response metadata. This is more
    accurate than computing from token counts + pricing tables.
    """
    total_cost = 0.0
    for msg in messages:
        if isinstance(msg, ModelResponse):
            try:
                if msg.provider_details:
                    cost = msg.provider_details.get("cost")
                    if cost is not None:
                        total_cost += float(cost)
            except Exception:
                pass
    return total_cost


def _should_retry_without_thinking(error: Exception) -> bool:
    """Check if an error suggests the model doesn't support thinking params."""
    error_str = str(error).lower()
    return any(marker in error_str for marker in _THINKING_ERROR_MARKERS)


def _try_parse(s) -> dict:
    """Try to parse a string as JSON dict, return empty dict on failure."""
    if isinstance(s, dict):
        return s
    try:
        result = json.loads(s)
        return result if isinstance(result, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


_GAME_ACTION_KEYS = {"inputs", "reasoning", "last_turn_succeeded", "memory_updates"}


def _try_parse_game_action(text) -> Optional[dict]:
    """If `text` parses to a JSON object with GameAction's full key set, return
    it. Used to recognize prompted-output TextParts as the same structured
    final_result that tool-mode emits — so dashboard + terminal display the
    parsed fields (Output / Reasoning / Last turn ok? / Memory) instead of one
    raw JSON blob."""
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        from src.core.patches import _robust_strip_markdown_fences
        cleaned = _robust_strip_markdown_fences(text)
    except Exception:
        cleaned = text
    try:
        parsed = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError, TypeError):
        return None
    if isinstance(parsed, dict) and _GAME_ACTION_KEYS.issubset(parsed.keys()):
        return parsed
    return None


def _truncate(text: str, max_len: int = 120) -> str:
    """Truncate text for terminal display."""
    text = text.replace("\n", " ").strip()
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."


def _print_trace_summary(turn_num: int, trace: list[dict]) -> None:
    """Print a structured summary of the turn's trace to the terminal."""
    step = 0

    def tag(extra: str = "") -> str:
        base = f"Turn {turn_num}"
        if step > 0:
            base += f", Step {step}"
        if extra:
            base += f", {extra}"
        return f"  [{base}]"

    for msg in trace:
        role = msg.get("role", "")

        if role == "system":
            print(f"{tag()} System prompt ({len(msg.get('content', ''))} chars)")

        elif role == "user":
            content = msg.get("content", "")
            lines = [l.strip() for l in content.split("\n") if l.strip() and not l.strip().startswith("[")]
            preview = lines[0] if lines else ""
            print(f"{tag()} Input ({len(content)} chars): {_truncate(preview, 80)}")

        elif role == "thinking":
            content = msg.get("content", "")
            first_line = content.split("\n")[0].strip() if content else ""
            print(f"{tag('Thinking')} {_truncate(first_line, 100)}")

        elif role == "tool_call":
            step += 1
            tool = msg.get("tool_name", "?")
            args = msg.get("args", "")

            if tool == "final_result":
                parsed = args if isinstance(args, dict) else _try_parse(args)
                action = parsed.get("inputs", "?")
                if isinstance(action, list):
                    action = "[" + ", ".join(str(a) for a in action) + "]"
                reasoning = parsed.get("reasoning", "")
                succeeded = parsed.get("last_turn_succeeded")
                if succeeded is True:
                    succeeded_str = "true"
                elif succeeded is False:
                    succeeded_str = "false"
                else:
                    succeeded_str = "null"
                memory = parsed.get("memory_updates", "")
                print(f"{tag('Output')} {action}")
                print(f"{tag('Last turn ok?')} {succeeded_str}")
                print(f"{tag('Reasoning')} {_truncate(reasoning, 200)}")
                print(f"{tag('Memory')} {_truncate(str(memory), 200) if memory else '(none)'}")
            else:
                if isinstance(args, dict):
                    args_preview = ", ".join(f"{k}={_truncate(str(v), 30)}" for k, v in args.items())
                else:
                    args_preview = _truncate(str(args), 60)
                print(f"{tag('Call')} {tool}({args_preview})")

        elif role == "tool_result":
            tool = msg.get("tool_name", "")
            if tool == "final_result":
                continue
            content = msg.get("content", "")
            print(f"{tag('Response')} {_truncate(str(content), 100)}")

        elif role == "retry":
            print(f"{tag('Retry')} {_truncate(msg.get('content', ''), 100)}")


class _TeeWriter:
    """Duplicates writes to both a stream and a file."""

    def __init__(self, original, log_file):
        self._original = original
        self._log_file = log_file

    def write(self, data):
        self._original.write(data)
        try:
            self._log_file.write(data)
            self._log_file.flush()
        except Exception:
            pass

    def flush(self):
        self._original.flush()
        try:
            self._log_file.flush()
        except Exception:
            pass

    def __getattr__(self, name):
        return getattr(self._original, name)


class TurnManager:
    """Orchestrates the turn loop: screenshot -> think -> act -> repeat."""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.max_turns = config.get("max_turns_per_task", 50)

        # These get set during setup
        self.emulator: Optional[EmulatorClient] = None
        self.state: Optional[StateManager] = None
        self.vision: Optional[VisionPipeline] = None
        self.logger: Optional[RunLogger] = None
        self.ocr: Optional[OCRRunner] = None
        self.agent, self.model_settings, self.fallback_models = create_agent(config)
        self.max_steps_per_turn = config.get("max_steps_per_turn", 10)
        self.max_turns_before_trim = config.get("max_turns_before_trim")
        self.historic_images_count = config.get("historic_images_count", 0)
        self.task_override_snapshot = config.get("task_override_snapshot", False)

        # Turn history
        self.turn_explanations: list[dict] = []
        # Ring buffer of (turn_number, PIL.Image) for the last K screenshots —
        # only populated when historic_images_count > 0. Bounded by K so memory
        # stays flat regardless of run length.
        self.turn_screenshots: list[tuple[int, Image.Image]] = []
        self.turn_number = 0

        # Tasks (loaded from run folder)
        self.tasks: Optional[dict] = None

        # Cost tracking
        self.total_cost_usd = 0.0
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.turn_costs: list[dict] = []

        # Run timing
        self._run_start_time: Optional[float] = None

    def setup(
        self,
        emulator: EmulatorClient,
        state: StateManager,
        vision: VisionPipeline,
        logger: RunLogger,
        ocr: Optional[OCRRunner] = None,
    ) -> None:
        """Inject dependencies."""
        self.emulator = emulator
        self.state = state
        self.vision = vision
        self.logger = logger
        self.ocr = ocr

        # Load tasks from run folder if present — UNLESS the config has
        # task_override_snapshot=true, in which case we ignore the snapshot's
        # task and let the user-message build fall through to config["task"]
        # (which matches what the system prompt already uses).
        tasks_path = logger.run_dir / "tasks.json"
        if tasks_path.exists() and not self.task_override_snapshot:
            with open(tasks_path) as f:
                self.tasks = json.load(f)
            snap_goal = (self.tasks or {}).get("goal", "?")
            print(f"  Task source: SNAPSHOT — goal: {snap_goal!r}")
        else:
            cfg_goal = (self.config.get("task") or {}).get("goal", "?")
            if tasks_path.exists() and self.task_override_snapshot:
                snap_goal = "?"
                try:
                    with open(tasks_path) as f:
                        snap_goal = json.load(f).get("goal", "?")
                except Exception:
                    pass
                print(
                    f"  Task source: CONFIG (override active) — goal: {cfg_goal!r} "
                    f"(snapshot goal {snap_goal!r} ignored)"
                )
            else:
                print(f"  Task source: CONFIG — goal: {cfg_goal!r}")

    def run_loop(self, max_turns: Optional[int] = None) -> None:
        """Run the turn loop synchronously."""
        asyncio.run(self._run_loop_async(max_turns))

    async def _run_loop_async(self, max_turns: Optional[int] = None) -> None:
        """Run the turn loop."""
        self._run_start_time = time.time()
        limit = max_turns or self.max_turns

        # Tee stdout to a terminal log file in the run folder
        self._terminal_log = open(self.logger.run_dir / "terminal.log", "w")
        self._orig_stdout = sys.stdout
        sys.stdout = _TeeWriter(self._orig_stdout, self._terminal_log)

        for _ in range(limit):
            self.turn_number += 1
            print(f"\n{'─'*60}")
            print(f"  Turn {self.turn_number}")
            print(f"{'─'*60}")

            result = await self._run_turn()
            if result is None:
                print(f"  [Turn {self.turn_number}] No result. Stopping.")
                break

            # Apply memory updates from the agent's output (string → dict)
            updates = {}
            if result.memory_updates and result.memory_updates.strip().lower() != "none":
                try:
                    parsed = json.loads(result.memory_updates)
                    if isinstance(parsed, dict):
                        updates = parsed
                except (json.JSONDecodeError, TypeError):
                    pass
            if updates:
                for key, value in updates.items():
                    if value is None or value == "":
                        self.state.delete_by_path(key)
                    else:
                        self.state.set_by_path(key, value)
                self.state.save()
                print(f"  [Turn {self.turn_number}] Memory: {json.dumps(updates)}")
                self.logger.log_state_change("memory_update", {
                    "updates": updates,
                })

            # Log the turn explanation
            explanation = {
                "action": result.inputs,
                "reasoning": result.reasoning,
                "last_turn_succeeded": result.last_turn_succeeded,
                "memory_updates": updates,
                "memory_updates_raw": result.memory_updates,
            }
            self.turn_explanations.append(explanation)
            self.logger.log_turn_explanation(self.turn_number, explanation)

            # Execute button presses and wait for screen to settle.
            # OCR captures are gated to this window only — no captures during
            # LLM thinking or during the next turn's vision call.
            try:
                action_display = "[" + ", ".join(result.inputs) + "]"
                print(f"  [Turn {self.turn_number}] Executing {action_display}...")
                if self.ocr and self.ocr.enabled:
                    self.ocr.set_active(True)
                self.emulator.press_button_list(result.inputs)
                self.logger.log_button_sequence(str(result.inputs))
                print(f"  [Turn {self.turn_number}] Waiting for screen to settle...")
                self.logger.log_custom("screen_settling", {"turn": self.turn_number})
                settle_duration = self.emulator.wait_for_stable_screen()
                self.logger.log_custom("screen_settled", {"turn": self.turn_number, "duration": round(settle_duration, 1)})
                print(f"  [Turn {self.turn_number}] Screen settled ({settle_duration:.1f}s)")
            except Exception as e:
                print(f"  [Turn {self.turn_number}] Execution error: {e}")
                self.logger.log_custom("action_error", {"error": str(e)})
                # Reset facing — we don't know where the player ended up
                self.emulator.facing = None
            finally:
                if self.ocr and self.ocr.enabled:
                    self.ocr.set_active(False)

        # Add VLM cost to total
        vlm_cost = self.vision.total_cost_usd if self.vision else 0.0
        self.total_cost_usd += vlm_cost

        ocr_cost = self.ocr.total_cost_usd if self.ocr else 0.0
        llm_cost = self.total_cost_usd - vlm_cost - ocr_cost
        print(f"\n{'═'*60}")
        print(f"  Run complete: {self.turn_number} turns")
        print(
            f"  Cost: ${self.total_cost_usd:.4f} "
            f"(LLM: ${llm_cost:.4f}, VLM: ${vlm_cost:.4f}, OCR: ${ocr_cost:.5f})"
        )
        print(f"  Tokens: {self.total_input_tokens} in / {self.total_output_tokens} out")
        print(f"{'═'*60}")

        # Write structured run summary
        self._write_run_summary()

        # Restore stdout and close terminal log
        sys.stdout = self._orig_stdout
        self._terminal_log.close()

    async def _run_turn(self) -> Optional[GameAction]:
        """Execute a single turn."""
        turn_start = time.time()
        t = self.turn_number
        self.logger.log_turn_start(t)

        # 1. Capture screenshot
        print(f"  [Turn {t}] Capturing screenshot...")
        screenshot = self.emulator.capture_screenshot(preprocess=True)
        self.logger.log_screenshot(screenshot, label=f"turn_{t}")

        # 2. Run vision pipeline
        vision_label = "VLM" if self.vision.vision_mode == "separate_vlm" else "Vision"
        print(f"  [Turn {t}] Running {vision_label}...")
        analysis = self.vision.analyze_screenshot(screenshot)
        vision_content = self.vision.format_for_llm(analysis)

        if "description" in analysis:
            desc = analysis["description"]
            self.logger.log_vlm_response(desc)
            print(f"  [Turn {t}] VLM: {_truncate(desc, 100)}")

            # Sync facing direction from VLM (more reliable than tracking)
            desc_lower = desc.lower()
            for direction in ("facing up", "facing down", "facing left", "facing right"):
                if direction in desc_lower:
                    self.emulator.facing = direction.split()[-1]
                    break

        # 3. Flush OCR buffer (background captures since last turn) + LLM cleanup
        ocr_text = ""
        ocr_raw: dict = {}
        if self.ocr and self.ocr.enabled:
            t_ocr = time.time()
            ocr_text, ocr_raw, ocr_usage, ocr_stats = self.ocr.flush_and_cleanup()
            cleanup_elapsed = time.time() - t_ocr
            ocr_cost = ocr_usage.get("cost_usd", 0.0)
            self.total_cost_usd += ocr_cost
            self.logger.log_custom("ocr_flush", {
                "turn": t,
                "raw": ocr_raw,
                "cleaned": ocr_text,
                "n_captures": len(ocr_raw),
                "duration": round(cleanup_elapsed, 2),  # cleanup call time (kept for back-compat)
                "cleanup_s": round(cleanup_elapsed, 2),
                "cost_usd": ocr_cost,
                "input_tokens": ocr_usage.get("input_tokens", 0),
                "output_tokens": ocr_usage.get("output_tokens", 0),
                "model": self.ocr.cleanup_model,
                "window_s": ocr_stats["window_s"],
                "attempts": ocr_stats["attempts"],
                "tesseract_runs": ocr_stats["tesseract_runs"],
                "hash_dupes": ocr_stats["hash_dupes"],
                "text_dupes": ocr_stats["text_dupes"],
                "empty_ocr": ocr_stats["empty_ocr"],
                "buffer_full": ocr_stats["buffer_full"],
            })
            if ocr_stats["attempts"] > 0 or ocr_raw:
                print(
                    f"  [Turn {t}] OCR: window={ocr_stats['window_s']:.1f}s "
                    f"| {ocr_stats['attempts']} polls → "
                    f"{ocr_stats['tesseract_runs']} tesseract "
                    f"({ocr_stats['hash_dupes']} hash-dup) → "
                    f"{len(ocr_raw)} kept "
                    f"({ocr_stats['text_dupes']} text-dup, {ocr_stats['empty_ocr']} empty"
                    + (f", {ocr_stats['buffer_full']} overflow" if ocr_stats['buffer_full'] else "")
                    + ")"
                )
                print(
                    f"  [Turn {t}] OCR cleanup: {cleanup_elapsed:.1f}s | ${ocr_cost:.5f} "
                    f"| {ocr_usage.get('input_tokens', 0)}→{ocr_usage.get('output_tokens', 0)} tokens"
                )

        # 4. Get current memory dictionary
        state_view = self.state.get_truncated_view()

        # 5. Build the user message
        # At this point self.turn_screenshots contains the last K *prior* turns'
        # screenshots — current turn t is intentionally NOT in the buffer yet,
        # since it's already passed in separately as the "current screen".
        user_message = self._build_turn_message(
            vision_content, ocr_text, state_view, screenshot,
        )

        # Now that the message is built, stash this turn's screenshot for the
        # NEXT turn's historic-image block. Capped at K so memory stays flat.
        if self.historic_images_count > 0:
            self.turn_screenshots.append((t, screenshot))
            if len(self.turn_screenshots) > self.historic_images_count:
                self.turn_screenshots = self.turn_screenshots[-self.historic_images_count:]

        # Log the text portion of the user message
        if isinstance(user_message, str):
            log_msg = user_message[:5000]
        else:
            # Multimodal list — extract text parts for logging
            log_msg = " ".join(str(p) for p in user_message if isinstance(p, str))[:5000]
        self.logger.log_custom("turn_user_message", {
            "turn": t,
            "message": log_msg,
        })

        # 6. Build deps
        deps = AgentDeps(
            emulator=self.emulator,
            state=self.state,
            vision=self.vision,
            logger=self.logger,
            ocr=self.ocr,
            current_screenshot=screenshot,
            turn_number=t,
        )

        # 7. Run the agent
        print(f"  [Turn {t}] Running LLM...")
        messages = []
        try:
            result, model_used = await self._run_agent_with_fallback(
                user_message, deps, messages
            )

            # 8. Log trace + cost
            trace = _serialize_messages(messages)
            turn_cost = _extract_cost_from_messages(messages)
            self.total_cost_usd += turn_cost

            # Print trace summary to terminal
            _print_trace_summary(t, trace)

            self.logger.log_custom("turn_trace", {
                "turn": t,
                "messages": trace,
                "model_used": model_used,
            })

            # Log usage
            tokens_str = ""
            if result.usage():
                usage = result.usage()
                self.total_input_tokens += usage.request_tokens or 0
                self.total_output_tokens += usage.response_tokens or 0
                tokens_str = f" | {usage.request_tokens}→{usage.response_tokens} tokens"
                self.logger.log_custom("turn_usage", {
                    "turn": t,
                    "request_tokens": usage.request_tokens,
                    "response_tokens": usage.response_tokens,
                    "total_tokens": usage.total_tokens,
                    "cost_usd": turn_cost,
                })

            duration = round(time.time() - turn_start, 1)
            print(f"  [Turn {t}] Done ({duration}s | ${turn_cost:.4f}{tokens_str})")

            self.turn_costs.append({
                "turn": t,
                "cost_usd": turn_cost,
                "model": model_used,
                "duration_s": duration,
            })

            return result.output

        except Exception as e:
            # Always log the trace even on failure
            if messages:
                trace = _serialize_messages(messages)
                turn_cost = _extract_cost_from_messages(messages)
                self.total_cost_usd += turn_cost
                _print_trace_summary(t, trace)
                self.logger.log_custom("turn_trace", {
                    "turn": t,
                    "messages": trace,
                    "error": str(e),
                })

            print(f"  [Turn {t}] ERROR: {e}")
            self.logger.log_custom("agent_error", {
                "error": str(e),
                "turn": t,
            })
            return None

    async def _run_agent_iter(self, user_message, deps, model, usage_limits, model_settings=None):
        """Run a single agent iteration with streaming. Returns (result, captured_messages)."""
        from pydantic_ai import capture_run_messages
        from pydantic_ai._agent_graph import CallToolsNode, ModelRequestNode

        with capture_run_messages() as captured:
            kwargs = {"model_settings": model_settings} if model_settings else {}
            async with self.agent.iter(
                user_message, deps=deps, model=model,
                usage_limits=usage_limits, **kwargs,
            ) as agent_run:
                async for node in agent_run:
                    if isinstance(node, CallToolsNode):
                        self._emit_node_events(node, deps)
                    elif isinstance(node, ModelRequestNode):
                        self._emit_retry_events(node, deps)
            return agent_run.result, list(captured)

    async def _run_agent_with_fallback(
        self,
        user_message,
        deps: AgentDeps,
        out_messages: list,
    ) -> tuple:
        """Run the agent, trying fallback models if the primary fails.

        Populates out_messages with the captured message history.
        Returns (result, model_id_used).
        """
        from pydantic_ai.usage import UsageLimits

        usage_limits = UsageLimits(request_limit=self.max_steps_per_turn)

        primary_model_id = self.config.get("llm_model", "")
        model_chain = [primary_model_id] + list(self.fallback_models)

        last_error = None

        for model_id in model_chain:
            model = OpenAIModel(model_id, provider="openrouter")

            try:
                result, captured = await self._run_agent_iter(
                    user_message, deps, model, usage_limits, self.model_settings
                )
                out_messages.extend(captured)
                return result, model_id

            except Exception as exc:
                last_error = exc
                logger.warning(f"Model {model_id} failed: {exc}")

                if self.model_settings and _should_retry_without_thinking(exc):
                    try:
                        logger.info(f"Retrying {model_id} without thinking params")
                        result, captured = await self._run_agent_iter(
                            user_message, deps, model, usage_limits
                        )
                        out_messages.extend(captured)
                        return result, model_id
                    except Exception as exc2:
                        last_error = exc2
                        logger.warning(f"Model {model_id} failed without thinking: {exc2}")

        raise last_error

    def _emit_retry_events(self, node, deps: AgentDeps) -> None:
        """Emit log events for retry prompts (validation failures)."""
        request = node.request
        if not request or not hasattr(request, 'parts'):
            return

        for part in request.parts:
            if isinstance(part, RetryPromptPart):
                content = str(part.content) if part.content else "Validation failed"
                self.logger.log_custom("output_retry", {
                    "content": content,
                    "turn": deps.turn_number,
                    "agent_id": deps.agent_id,
                })

    def _emit_node_events(self, node, deps: AgentDeps) -> None:
        """Emit log events for a CallToolsNode's content (thinking, tool calls, output)."""
        response = node.model_response
        if not response or not hasattr(response, 'parts'):
            return

        for part in response.parts:
            if isinstance(part, ThinkingPart) and part.content:
                self.logger.log_custom("llm_thinking", {
                    "content": part.content,
                    "turn": deps.turn_number,
                    "agent_id": deps.agent_id,
                })
            elif isinstance(part, TextPart) and part.content:
                # In prompted-output mode the GameAction JSON arrives as a
                # TextPart. Emit the same llm_output + memory_update_output
                # events tool-mode emits, so the dashboard renders identical
                # labeled fields. Fall back to llm_text for genuine prose.
                parsed_action = _try_parse_game_action(part.content)
                if parsed_action is not None:
                    self.logger.log_custom("llm_output", {
                        "args": json.dumps(parsed_action),
                        "turn": deps.turn_number,
                        "agent_id": deps.agent_id,
                    })
                    mem_raw = parsed_action.get("memory_updates", "")
                    self.logger.log_custom("memory_update_output", {
                        "content": mem_raw if mem_raw else "(no changes)",
                        "turn": deps.turn_number,
                        "agent_id": deps.agent_id,
                    })
                else:
                    self.logger.log_custom("llm_text", {
                        "content": part.content,
                        "turn": deps.turn_number,
                        "agent_id": deps.agent_id,
                    })
            elif isinstance(part, ToolCallPart):
                tool_name = part.tool_name
                args = part.args
                if tool_name == 'final_result':
                    self.logger.log_custom("llm_output", {
                        "args": args if isinstance(args, str) else json.dumps(args),
                        "turn": deps.turn_number,
                        "agent_id": deps.agent_id,
                    })
                    # Emit memory update as a separate event for the dashboard
                    parsed_args = args if isinstance(args, dict) else {}
                    if isinstance(args, str):
                        try:
                            parsed_args = json.loads(args)
                        except (json.JSONDecodeError, TypeError):
                            parsed_args = {}
                    mem_raw = parsed_args.get("memory_updates", "")
                    self.logger.log_custom("memory_update_output", {
                        "content": mem_raw if mem_raw else "(no changes)",
                        "turn": deps.turn_number,
                        "agent_id": deps.agent_id,
                    })

    def _build_turn_message(
        self,
        vision_content: list[dict],
        ocr_text: str,
        state_view: dict,
        current_screenshot: Image.Image,
    ):
        """Build the user message for a turn.

        Layout follows OpenRouter's "text first, then images" recommendation:
        all explanatory text is concatenated into one block, then any images
        are appended at the end with explicit per-image labels immediately
        preceding each one. The current-turn screenshot is ALWAYS last so it
        sits closest to the model's decision point.

        Returns:
        - str for separate_vlm mode (LLM never sees raw images).
        - list[UserContent] for direct_multimodal — always at least
          [text, label, current_image]. With historic_images_count = K and
          enough prior turns, becomes
          [text, hist_label_1, hist_img_1, ..., hist_label_K, hist_img_K,
           current_label, current_image].
        """
        text_parts: list[str] = []
        current_image_url: Optional[str] = None

        # Vision content. In direct_multimodal mode this yields a "[Game Screen]"
        # text marker plus a data URL — we strip the marker (the explicit labels
        # below carry the role information) and keep the URL for the image-tail.
        for block in vision_content:
            if block["type"] == "text":
                # Skip the bare "[Game Screen]" placeholder — replaced by the
                # explicit current-screen label appended at the end.
                if block["text"].strip() != "[Game Screen]":
                    text_parts.append(block["text"])
            elif block["type"] == "image_url":
                current_image_url = block["image_url"]["url"]

        # Heads-up about the image tail. Only emitted in direct_multimodal mode
        # so the model knows what to expect at the end of the message.
        if current_image_url is not None:
            n_historic = len(self.turn_screenshots) if self.historic_images_count > 0 else 0
            n_total_images = n_historic + 1
            if n_historic > 0:
                text_parts.append(
                    f"\n## Screens\n"
                    f"This message ends with {n_total_images} screenshots in chronological order. "
                    f"The first {n_historic} are historic — the screen the agent saw at the START "
                    f"of each of the last {n_historic} turn(s), BEFORE pressing the actions listed "
                    f"under '## Previous Turns'. The LAST screenshot is the CURRENT turn — that "
                    f"is what you must reason about and act on now. Each image is preceded by an "
                    f"explicit label."
                )
            else:
                text_parts.append(
                    "\n## Screen\n"
                    "The current-turn screenshot is shown at the end of this message — that is "
                    "what you must reason about and act on."
                )

        # OCR (cleaned text captured between last turn and now)
        if ocr_text:
            text_parts.append(
                f"\n## Recent OCR Text\n"
                f"Cleaned text captured from the screen between the last turn and now. "
                f"May include scrolling dialogue, menu labels, and UI text. Use as ground-truth "
                f"for exact character sequences; trust the screenshot for spatial layout.\n\n"
                f"{ocr_text}"
            )

        # Memory dictionary
        state_json = json.dumps(state_view, indent=2)
        text_parts.append(f"\n## Memory\n```json\n{state_json}\n```")

        # Turn history. Each turn k's `did this turn succeed?` is the value the
        # FOLLOWING turn (k+1) wrote into its `last_turn_succeeded` field. The
        # most recent prior turn has no follower yet, so its grade is left blank
        # for the current turn to fill in via `last_turn_succeeded`.
        # Turns whose screenshot is also included in the image tail get a
        # cross-reference line so the model can bind text history ↔ image.
        historic_turn_nums = {
            turn_num for (turn_num, _) in self.turn_screenshots
        } if self.historic_images_count > 0 else set()
        if self.turn_explanations:
            history = "\n## Previous Turns"
            n_total = len(self.turn_explanations)
            trim = self.max_turns_before_trim
            if trim is not None and n_total > trim:
                start = n_total - trim
                history += (
                    f"\n\n_(Earlier turns have been truncated. "
                    f"Showing the last {trim} of {n_total} turns.)_"
                )
            else:
                start = 0
            visible = self.turn_explanations[start:]
            n_visible = len(visible)
            for j, exp in enumerate(visible):
                turn_num = start + j + 1
                action = exp.get('action', [])
                if isinstance(action, list):
                    action_str = ", ".join(action)
                else:
                    action_str = str(action)
                reasoning = exp.get('reasoning', '')
                next_idx = j + 1
                if next_idx < n_visible:
                    grade = visible[next_idx].get('last_turn_succeeded')
                    if grade is True:
                        grade_str = "true"
                    elif grade is False:
                        grade_str = "false"
                    elif grade is None:
                        grade_str = "null"
                    else:
                        grade_str = "(missing)"
                else:
                    grade_str = "<for you to decide this turn>"
                history += f"\n\n### Turn {turn_num}"
                history += f"\n- actions: {action_str}"
                history += f"\n- reasoning: {reasoning}"
                history += f"\n- did this turn succeed?: {grade_str}"
                if turn_num in historic_turn_nums:
                    history += (
                        f"\n- (screenshot from the START of turn {turn_num}, "
                        f"BEFORE these actions were pressed, is included in the image tail "
                        f"below — compare to the CURRENT screen image)"
                    )
            text_parts.append(history)

        # Task (tasks.json overrides config task)
        task = self.tasks or self.config.get("task", {})
        if isinstance(task, str):
            task = {"goal": task}
        goal = task.get("goal", "Play the game.")
        desc = task.get("description", "")
        task_text = f"**Goal:** {goal}"
        if desc:
            task_text += f"\n{desc}"
        text_parts.append(f"\n## Current Task\n{task_text}")

        text_parts.append(
            "\nOutput your action (inputs) and update memory_updates with any new information."
        )

        combined_text = "\n".join(text_parts)

        # Separate VLM: no images, just return the text.
        if current_image_url is None:
            return combined_text

        # Direct multimodal: text first, then image tail.
        # Historic images go oldest → newest, then the current screenshot last.
        parts: list[UserContent] = [combined_text]
        n_total_images = len(self.turn_screenshots) + 1
        idx = 0
        for hist_turn_num, hist_image in self.turn_screenshots:
            idx += 1
            actions_str = self._lookup_actions(hist_turn_num)
            parts.append(
                f"=== SCREENSHOT {idx} of {n_total_images} — Turn {hist_turn_num} "
                f"(BEFORE actions). This is what the agent saw at the START of "
                f"turn {hist_turn_num}, before pressing [{actions_str}]. ==="
            )
            parts.append(ImageUrl(url=self.vision.image_to_data_url(hist_image)))
        idx += 1
        parts.append(
            f"=== SCREENSHOT {idx} of {n_total_images} — CURRENT (Turn {self.turn_number}). "
            f"This is what you see NOW. Decide your next action based on THIS screen. ==="
        )
        parts.append(ImageUrl(url=current_image_url))
        return parts

    def _lookup_actions(self, turn_num: int) -> str:
        """Return the comma-joined action list for a past turn, or '?' if unknown."""
        idx = turn_num - 1
        if 0 <= idx < len(self.turn_explanations):
            action = self.turn_explanations[idx].get("action", [])
            if isinstance(action, list):
                return ", ".join(action)
            return str(action)
        return "?"

    def _write_run_summary(self) -> None:
        """Write a structured run_summary.json to the run folder."""
        duration = time.time() - self._run_start_time if self._run_start_time else 0

        summary = {
            "session": {
                "llm_alias": self.config.get("_llm_alias"),
                "llm_model": self.config.get("llm_model", ""),
                "vlm_model": self.config.get("vlm_model", ""),
                "vision_mode": self.config.get("vision_mode", ""),
                "thinking": self.config.get("thinking"),
                "fallback_models": self.fallback_models,
                "task": (self.tasks or self.config.get("task", {})).get("goal", ""),
                "total_turns": self.turn_number,
                "duration_seconds": round(duration, 1),
                "started_at": time.strftime(
                    "%Y-%m-%dT%H:%M:%S",
                    time.localtime(self._run_start_time),
                ) if self._run_start_time else None,
            },
            "cost": {
                "total_usd": round(self.total_cost_usd, 6),
                "llm_usd": round(
                    self.total_cost_usd
                    - (self.vision.total_cost_usd if self.vision else 0)
                    - (self.ocr.total_cost_usd if self.ocr else 0),
                    6,
                ),
                "vlm_usd": round(self.vision.total_cost_usd if self.vision else 0, 6),
                "ocr_usd": round(self.ocr.total_cost_usd if self.ocr else 0, 6),
                "total_input_tokens": self.total_input_tokens,
                "total_output_tokens": self.total_output_tokens,
                "per_turn": self.turn_costs,
            },
            "turns": [
                {
                    "turn": i + 1,
                    "action": exp.get("action", ""),
                    "reasoning": exp.get("reasoning", ""),
                    "last_turn_succeeded": exp.get("last_turn_succeeded"),
                }
                for i, exp in enumerate(self.turn_explanations)
            ],
        }

        summary_path = self.logger.run_dir / "run_summary.json"
        with open(summary_path, "w") as f:
            json.dump(summary, f, indent=2)

        print(f"Run summary saved: {summary_path}")
