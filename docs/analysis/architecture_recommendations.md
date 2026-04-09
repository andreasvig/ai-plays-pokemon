# Architecture Recommendations from Translator & Program Enrichment

Lessons learned from two mature projects that share our stack (Pydantic AI + OpenRouter).

---

## 1. Configuration: Pydantic Settings + Strict Validation

**Both projects use Pydantic `BaseSettings` with `extra="forbid"`** instead of raw YAML dicts.

**Current (AI Plays Pokemon):** `load_config()` returns a plain `dict[str, Any]` — typos in config keys are silent, no autocomplete, no validation.

**Recommendation:** Define a `Settings` class with typed fields. Catches mistakes at startup.

```python
class EmulatorConfig(StrictModel):
    rom_path: str
    host: str = "127.0.0.1"
    port: int = 8888

class Settings(BaseSettings):
    model_config = ConfigDict(extra="forbid")
    llm_model: str
    vlm_model: str
    emulator: EmulatorConfig
    # ...
```

**Priority: Medium.** Nice quality-of-life improvement but not blocking anything.

---

## 2. Config Singleton Pattern

**Both projects:** `get_settings()` with `@lru_cache` or a module-level `_settings` variable. Loaded once, frozen forever.

**Current:** Config is loaded in `test_phase5.py` and passed as a dict through constructors. Every module re-reads keys from this dict.

**Recommendation:** Singleton `get_settings()` so any module can access config without threading it through every function.

**Priority: Low.** Current approach works fine for a single-entry-point CLI tool.

---

## 3. Agent Registry & Metadata Auto-Collection

**Translator:** Single `agents/__init__.py` exports all agents, deps, and metadata. `_collect_agent_meta()` auto-harvests retries, models, usage limits from agent modules — these flow into `run_log.json` with zero manual duplication.

**Current:** Single agent defined in `agent.py`. No metadata captured about agent config.

**Recommendation:** When Phase 7 adds sub-agents, use this pattern. Export all agents from one place. Auto-collect metadata (model, retries, tools) for the run log.

**Priority: High for Phase 7.** Essential when we have multiple agent types.

---

## 4. Model Fallback Chain

**Translator:** `_run_agent_with_model_fallback()` tries models in order: `[Gemini Pro, Gemini Flash, Grok-4]`. If one fails, tries next. Also has a "thinking parameter fallback" — if a model rejects reasoning params, retries without them.

**Program Enrichment:** Same pattern via `execute_agent()` with configurable model list.

**Current:** Single model, no fallback. If it fails, the turn fails.

**Recommendation:** Add a fallback chain in config:
```yaml
llm_models:
  - google/gemini-3-flash-preview
  - google/gemini-2.5-flash  # fallback
```
Try each in order. Also add thinking-parameter fallback (retry without `reasoning` if model rejects it).

**Priority: Medium.** Makes runs more resilient to model outages.

---

## 5. Structured Run Logging (JSON, not JSONL)

**Translator:** `run_log.json` is a single structured JSON file with `session`, `pipeline`, `agents`, `results[]`, `summary`. Each result has `trace`, `retry_summary`, `cost`, `generation_ids`. Incremental flush on each row completion.

**Program Enrichment:** Metrics are structured `SampleMetric` and `BatchMetric` Pydantic models emitted to pluggable backends.

**Current:** `events.jsonl` is a flat stream of heterogeneous events. The report generator reconstructs structure by parsing event types. No cost tracking. No retry summaries.

**Recommendation:** Keep JSONL for raw events (crash-safe), but also write a structured `run_summary.json` at run end with:
- Session metadata (models used, config snapshot, duration)
- Per-turn summaries (not raw traces — those stay in JSONL)
- Total token usage and cost
- Final state snapshot (already added)

**Priority: Medium.** Improves observability without replacing existing logging.

---

## 6. Cost Tracking from OpenRouter

**Both projects** extract cost from `provider_details["cost"]` on `ModelResponse` objects. OpenRouter includes actual USD cost in response headers — no need to compute from token counts.

**Current:** We log token usage but not cost.

**Recommendation:** Extract cost from provider_details in our trace serialization. Add to run summary. Minimal code change — the data is already there in the Pydantic AI response.

```python
# In _serialize_messages or a new helper:
if msg.provider_details:
    cost = msg.provider_details.get("cost")
    if cost is not None:
        total_cost += float(cost)
```

**Priority: High.** Nearly free to implement, very useful for tracking spend across runs.

---

## 7. Deterministic Checks Before LLM Calls

**Translator:** Three programmatic gates (calque detection, gender consistency, length validation) run *before* sending to the reviewer agent. Prevents wasting LLM calls on obviously bad output.

**Current:** No programmatic validation of agent output. If the agent outputs nonsense button presses, we just execute them.

**Recommendation:** Add lightweight validation after `GameAction` parsing:
- Validate button codes are real buttons
- Warn if action is identical to last N actions (stuck detection)
- Validate state tool calls produced actual changes

**Priority: Medium for Phase 7.** Stuck detection is already planned.

---

## 8. Hierarchical Error Codes

**Program Enrichment:** Errors use hierarchical codes like `llm:rate-limited`, `tool-failure:web-search:auth-error`. These flow into metrics for dashboard aggregation.

**Current:** Errors are logged as free-text strings.

**Recommendation:** Define error categories for common failure modes:
- `llm:api-error`, `llm:validation-error`, `llm:timeout`
- `emulator:socket-error`, `emulator:timeout`
- `vision:vlm-error`, `vision:ocr-error`
- `agent:stuck`, `agent:max-retries`

Log these as structured codes. Makes it easy to filter and count failure types in reports.

**Priority: Low.** Useful at scale, overkill for current iteration speed.

---

## 9. Deps `copy_fresh()` Pattern

**Translator:** `ResearcherDeps.copy_fresh()` shares immutable config but resets mutable counters. Clean separation between shared state and per-run state.

**Current:** `AgentDeps` is created fresh each turn with all fields set. No distinction between shared and per-turn state.

**Recommendation:** When Phase 7 adds sub-agents, use this pattern. Sub-agent deps should share emulator/vision/logger but get fresh turn counters.

```python
@dataclass
class AgentDeps:
    # Shared (immutable per run)
    emulator: EmulatorClient
    state: StateManager
    vision: VisionPipeline
    logger: RunLogger

    # Per-turn (mutable)
    current_screenshot: Any = None
    turn_number: int = 0

    def for_subtask(self, agent_id: str) -> "AgentDeps":
        """Create deps for a sub-agent: shared infra, fresh turn state."""
        return AgentDeps(
            emulator=self.emulator,
            state=self.state,
            vision=self.vision,
            logger=self.logger,
            turn_number=0,
            agent_id=agent_id,
        )
```

**Priority: High for Phase 7.**

---

## 10. Generic Batch/Workflow Orchestration

**Program Enrichment:** `FeatureSpec` protocol + generic `run_feature()` means every feature gets concurrency control, metrics, eval tracking, and error handling for free. New features implement a minimal spec.

**Current:** `TurnManager` is a single monolithic class.

**Recommendation:** When building the task system, consider a `TaskSpec` protocol:
```python
@dataclass
class TaskSpec:
    name: str
    max_turns: int
    system_prompt: str
    run_turn: Callable  # The turn execution logic
    is_complete: Callable  # Completion check
    on_stuck: Callable  # What to do when max_turns exceeded
```

This would let different task types (battle, navigation, menu interaction) share the turn loop infrastructure while customizing behavior.

**Priority: Medium for Phase 7.** Depends on whether sub-agents need different turn loop behavior.

---

## 11. Prompt Template Substitution

**Translator:** `fill_prompt(template, **kwargs)` replaces `{{key}}` placeholders in YAML prompts. Avoids f-string conflicts with JSON examples in prompts.

**Current:** System prompt is a static string in config.yaml.

**Recommendation:** Add simple template substitution for Phase 8 prompt engineering:
```yaml
system_prompt: |
  You are playing {{game_name}}.
  Your current task: {{current_task}}
  Available buttons: {{button_list}}
```

**Priority: High for Phase 8.** Makes prompts dynamic without code changes.

---

## 12. Trace Capture with `capture_run_messages()`

**Translator:** Uses Pydantic AI's `capture_run_messages()` context manager to capture all LLM exchanges, even when the agent call fails.

**Current:** We access `result.all_messages()` after a successful run. If the agent errors, we lose the trace.

**Recommendation:** Wrap agent runs in `capture_run_messages()`:
```python
from pydantic_ai import capture_run_messages

messages = []
try:
    with capture_run_messages() as messages:
        result = await self.agent.run(user_message, **run_kwargs)
finally:
    # Always have the trace, even on failure
    trace = _serialize_messages(messages)
    self.logger.log_custom("turn_trace", {"turn": self.turn_number, "messages": trace})
```

**Priority: High.** Simple change, big observability win for debugging failed turns.

---

## Summary: Priority Ranking

### Do Now (before/during Phase 7)
1. **Cost tracking from provider_details** — nearly free, very useful
2. **`capture_run_messages()` for error traces** — simple, high-value
3. **Deps `copy_fresh()` for sub-agents** — needed for Phase 7

### Do for Phase 7-8
4. **Agent registry pattern** — when multiple agent types exist
5. **Prompt template substitution** — for Phase 8 prompt engineering
6. **Model fallback chain** — resilience for longer runs
7. **Structured run summary JSON** — better post-run analysis

### Do Later
8. **Pydantic Settings for config** — quality of life
9. **Deterministic output validation** — stuck detection etc.
10. **Hierarchical error codes** — useful at scale
11. **Generic TaskSpec orchestration** — if task types diverge
12. **Config singleton** — if architecture grows
