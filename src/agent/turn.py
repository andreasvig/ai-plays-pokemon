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
                i_saw = parsed.get("i_saw", "")
                i_did = parsed.get("i_did", "")
                i_expect = parsed.get("i_expect", "")
                memory = parsed.get("memory_updates", "")
                print(f"{tag('Output')} {action}")
                print(f"{tag('Saw')} {_truncate(i_saw, 100)}")
                print(f"{tag('Did')} {_truncate(i_did, 100)}")
                print(f"{tag('Expect')} {_truncate(i_expect, 100)}")
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

        # Turn history
        self.turn_explanations: list[dict] = []
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

        # Load tasks from run folder if present
        tasks_path = logger.run_dir / "tasks.json"
        if tasks_path.exists():
            with open(tasks_path) as f:
                self.tasks = json.load(f)

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
                "i_saw": result.i_saw,
                "i_did": result.i_did,
                "i_expect": result.i_expect,
                "action": result.inputs,
                "memory_updates": updates,
                "memory_updates_raw": result.memory_updates,
            }
            self.turn_explanations.append(explanation)
            self.logger.log_turn_explanation(self.turn_number, explanation)

            # Execute button presses and wait for screen to settle
            try:
                action_display = "[" + ", ".join(result.inputs) + "]"
                print(f"  [Turn {self.turn_number}] Executing {action_display}...")
                self.emulator.press_button_list(result.inputs)
                self.logger.log_button_sequence(str(result.inputs))
                print(f"  [Turn {self.turn_number}] Waiting for screen to settle...")
                self.emulator.wait_for_stable_screen()
            except Exception as e:
                print(f"  [Turn {self.turn_number}] Execution error: {e}")
                self.logger.log_custom("action_error", {"error": str(e)})
                # Reset facing — we don't know where the player ended up
                self.emulator.facing = None

        # Add VLM cost to total
        vlm_cost = self.vision.total_cost_usd if self.vision else 0.0
        self.total_cost_usd += vlm_cost

        print(f"\n{'═'*60}")
        print(f"  Run complete: {self.turn_number} turns")
        print(f"  Cost: ${self.total_cost_usd:.4f} (LLM: ${self.total_cost_usd - vlm_cost:.4f}, VLM: ${vlm_cost:.4f})")
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

        # 3. Get OCR buffer
        ocr_text = ""
        if self.ocr:
            ocr_buffer = self.ocr.get_buffer()
            if ocr_buffer:
                ocr_text = "\n".join(ocr_buffer)
                self.ocr.clear_buffer()

        # 4. Get current memory dictionary
        state_view = self.state.get_truncated_view()

        # 5. Build the user message
        user_message = self._build_turn_message(
            vision_content, ocr_text, state_view
        )

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
    ):
        """Build the user message for a turn.

        Returns str for text-only (separate_vlm) or list[UserContent] for
        direct_multimodal (includes ImageUrl alongside text).
        """
        text_parts = []
        image_url: Optional[ImageUrl] = None

        # Vision content
        for block in vision_content:
            if block["type"] == "text":
                text_parts.append(block["text"])
            elif block["type"] == "image_url":
                # Direct multimodal — include the image as an ImageUrl part
                image_url = ImageUrl(url=block["image_url"]["url"])

        # OCR
        if ocr_text:
            text_parts.append(f"\n## OCR Text\n{ocr_text}")

        # Memory dictionary
        state_json = json.dumps(state_view, indent=2)
        text_parts.append(f"\n## Memory\n```json\n{state_json}\n```")

        # Turn history
        if self.turn_explanations:
            history = "\n## Previous Turns"
            for i, exp in enumerate(self.turn_explanations, 1):
                action = exp['action']
                if isinstance(action, list):
                    action = "[" + ", ".join(action) + "]"
                history += f"\n### Turn {i} — {action}"
                history += f"\n- **I saw:** {exp['i_saw']}"
                history += f"\n- **I did:** {exp['i_did']}"
                history += f"\n- **I expected:** {exp['i_expect']}"
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

        text_parts.append("\nOutput your action (inputs) and update memory_updates with any new information.")

        combined_text = "\n".join(text_parts)

        if image_url is not None:
            # Direct multimodal: return list with image + text
            return [image_url, combined_text]
        else:
            # Separate VLM: return plain string
            return combined_text

    def _write_run_summary(self) -> None:
        """Write a structured run_summary.json to the run folder."""
        duration = time.time() - self._run_start_time if self._run_start_time else 0

        summary = {
            "session": {
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
                "llm_usd": round(self.total_cost_usd - (self.vision.total_cost_usd if self.vision else 0), 6),
                "vlm_usd": round(self.vision.total_cost_usd if self.vision else 0, 6),
                "total_input_tokens": self.total_input_tokens,
                "total_output_tokens": self.total_output_tokens,
                "per_turn": self.turn_costs,
            },
            "turns": [
                {
                    "turn": i + 1,
                    "action": exp.get("action", ""),
                    "i_saw": exp.get("i_saw", ""),
                    "i_did": exp.get("i_did", ""),
                    "i_expect": exp.get("i_expect", ""),
                }
                for i, exp in enumerate(self.turn_explanations)
            ],
        }

        summary_path = self.logger.run_dir / "run_summary.json"
        with open(summary_path, "w") as f:
            json.dump(summary, f, indent=2)

        print(f"Run summary saved: {summary_path}")
