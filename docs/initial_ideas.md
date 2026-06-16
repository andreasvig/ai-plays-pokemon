# AI Plays Pokemon - Initial Ideas Document

> **Historical document.** The original vision/brainstorm, kept for genesis
> context. Some ideas here were dropped or evolved — the current system is
> documented in [control-center.md](control-center.md),
> [benchmark.md](benchmark.md), and [cli.md](cli.md).

## Core Concept

Build an AI agent harness that allows an LLM/VLM to play a Pokemon game.

## Core Philosophies

**1. Human-Level Information Only**
The agent receives only what a human player would see: the screen. No reading game memory, RAM values, internal state data, or emulator APIs for game state. If a human can't know it from looking at the screen, the agent can't know it either. Many other "LLM plays Pokemon" projects rely on extracted in-game data (exact HP numbers from memory, map coordinates from RAM, etc.) - this project explicitly rejects that approach.

**2. Game Agnostic Design**
While Pokemon is the first target, the harness should not be hardcoded to Pokemon. The architecture (vision pipeline, free-form state, task system, turn loop) should work for any game in principle. Game-specific knowledge lives in prompts and agent-managed state, not in the harness code.

**3. The Agent Manages Its Own Knowledge**
The agent decides what to remember, how to structure its state, and what to hide/surface. The harness provides the tools (read, write, VLM, OCR) but does not impose structure. The agent is responsible for building and maintaining its own understanding of the game world.

## Architecture: Vision Pipeline (Configurable)

Two modes, selectable via config:

- **Separate VLM mode:** A small, cheap VLM takes screenshots and produces a structured text description. The reasoning LLM receives only this text description to make decisions. Useful when the reasoning model is text-only, or when using a dedicated vision model is cheaper/faster.

- **Direct multimodal mode:** The reasoning LLM receives the raw screenshot directly and interprets it itself. No separate VLM step. Useful when the reasoning model is multimodal and capable enough to handle vision natively.

Both modes should be testable to compare performance and cost.

## Agent State Management (Confirmed)

### Free-Form State

The agent manages its own state in a single JSON file, accessed exclusively through tool calls. There are no predefined schemas - the agent decides what keys to create and how to structure its data. This keeps the system game-agnostic.

The system prompt guides the agent on what kinds of information are worth tracking (e.g., party info, inventory, map knowledge), but the agent controls the structure entirely. The harness owns the file; the tools are the API.

### State Visibility: The `_hide` System

At the start of each turn, the agent receives a summary view of its global state. To keep this summary concise, the agent can control what is visible vs. hidden using a `_hide` flag on any key in its state data.

**Rules:**
- `_hide: true` on a key replaces everything inside that key with `<hidden>`
- Hide cascades: if a parent is hidden, the entire subtree is hidden
- Key names are always visible so the agent knows what data exists
- The agent can read the full (unhidden) file via a tool call when it needs the details

**Example state:**

```json
{
  "party": {
    "charmander": {
      "_hide": true,
      "hp": 20,
      "moves": ["Scratch", "Ember"],
      "notes": "powerhouse, use first"
    },
    "pidgey": {
      "_hide": true,
      "hp": 15,
      "moves": ["Gust"]
    }
  },
  "current_location": "Route 1"
}
```

**Rendered at turn start:**

```json
{
  "party": {
    "charmander": "<hidden>",
    "pidgey": "<hidden>"
  },
  "current_location": "Route 1"
}
```

The agent sees it has Charmander and Pidgey, and it's on Route 1, but the detailed stats are hidden until explicitly requested. The agent itself decides what to hide and unhide as it plays.

## Naming Convention

- **Turn:** The big unit. One full cycle of: receive new VLM game state -> think -> act. "It's the agent's turn."
- **Step:** The small unit. Individual actions within a turn (reading a file, calling the VLM, editing inventory, etc.).

## Agent Tools (Confirmed)

### Information Tools

- **`ask_vlm(question)`** - Ask the VLM a follow-up question about the current screenshot. E.g., "What are the movement coordinates to that item?"
- **`web_search(query)`** - Search the web for help. Configurable, can be disabled.

### State File Tools

The agent's state is a single JSON file managed entirely through tool calls. The agent never touches the file directly.

- **`read_state(keys: [...])`** - Read full (unhidden) content for one or more keys. Bulk operation.
- **`edit_state(edits: {key: value, ...})`** - Edit one or more keys in a single call. Bulk operation.
- **`add_state(key, value)`** - Create a new key. Must have read the parent key first.
- **`delete_state(key)`** - Remove a key. Must have seen the key's content first.
- **`move_state(source, destination)`** - Move a key to a new location. Does NOT require reading the source's full content (only needs to know it exists).
- **`set_hide(key, hide: bool)`** - Control whether a key is hidden in the turn-start summary.

**Visibility-aware safety rule:** You can only edit, delete, or add-under a key whose content you have **seen** this turn. Content is "seen" if:
1. It was **visible** in the truncated turn-start state summary (not hidden), OR
2. It was explicitly **read** via `read_state()` during this turn

This means: if `party` is not hidden and its children are visible at turn start, the agent can immediately edit them without a read call. But if `party.charmander` is hidden, the agent must `read_state(["party.charmander"])` before it can edit it. Move is the exception - it only needs to know the key exists (visible in the summary), not its contents.

### Output Types

Each turn must end with exactly one of these outputs:

1. **`game_action(inputs: "RRRRAAA")`** - One or more game button presses, executed blindly in sequence.
2. **`create_subtask(task_description)`** - Spawn a sub-agent to handle a focused task. Parent agent is paused until the sub-agent returns.
3. **`return_to_parent(status, summary)`** - Return control to the parent agent.
   - `status: "success"` - Task completed as requested.
   - `status: "failed"` - Task could not be completed (e.g., whiteout, stuck, impossible).
   - `status: "other"` - Circumstances changed significantly and the parent needs to reassess. Not a failure, but the task can't continue as-is. E.g., an unexpected cutscene, a rival battle, or the agent needs information it doesn't have.

### Turn Explanation

After every output, the agent also produces a structured summary:
- **I saw:** (what the game state looked like)
- **I thought:** (reasoning)
- **I did:** (what action/output was taken and why)

## Turn Loop

1. **Turn start:** Agent receives the current screenshot (via VLM or direct), OCR log, truncated state summary, and previous turn explanations.
2. **Steps (tool calls):** The agent can use any combination of tools (read state, ask VLM, web search, edit state, etc.).
3. **Output:** The agent produces exactly one output (game action, create subtask, or return to parent).
4. **Turn explanation:** The agent writes its "I saw / I thought / I did" summary.
5. **Context cleanup:** Only the turn explanation is saved. All raw tool call history, VLM output, and intermediate data from this turn is discarded.
6. Next turn begins (new screenshot taken if game action was performed, or sub-agent runs if subtask was created).

## Context Management (Confirmed)

Each turn, the agent receives:
- The current VLM game state (new)
- The history of turn explanations from previous turns (accumulated "I saw / I thought / I did" log)
- Access to shared global state files

The raw conversation history (tool calls, VLM responses, file contents) from previous turns is **not** carried forward. Only the compressed turn explanations persist as the agent's local memory.

### Configurable Variables

- **Max turns before trim (optional):** After this many turns, older turn explanations can be trimmed/summarized. The max-turns-per-task limit may make this unnecessary in practice.

## Task System (Confirmed)

### Top-Level Task

Set by a human. Examples: "Beat the Elite 4", "Complete the Pokedex", "Beat Brock", "Capture a Pokemon". When the top-level task is completed, the agent awaits a new task from the human.

### Recursive Task Decomposition

A task setter evaluates whether a task is small enough to be directly acted on. If not, it breaks it down into a sub-task. This repeats recursively until the task is actionable.

Example chain: "Beat the game" -> "Defeat Brock" -> "Get a starter Pokemon"

The task setter and player are the **same agent**. Every agent in the tree is both a task setter (can spawn sub-agents) and a player (can take game actions). The distinction is the level of abstraction they operate at.

### Sub-Agent Spawning

When an agent encounters something that warrants a focused sub-task (e.g., a wild Pokemon battle during route navigation), it spawns a sub-agent via a tool call. The parent agent's execution is **paused/blocked** until the sub-agent returns.

The sub-agent receives:
- The full shared global state (global files, task hierarchy, system prompts)
- Its own task description
- **Not** the parent's local conversation history

The sub-agent does NOT get the parent's chat history. It works from the shared global state, which gives it all the context it needs without inheriting irrelevant conversation logs.

**When a sub-agent returns:** The parent agent resumes with a fresh screenshot and the current state summary (which the sub-agent may have modified). The state doesn't need re-reading since the sub-agent's final state IS the current state. The parent receives the sub-agent's return status and summary as the result of that turn.

### Task Completion

Sub-agents return to their parent with:
- **Status:** `completed`, `failed`, or `other`
- **Summary:** What happened (e.g., "Successfully defeated Rattata, Charmander lost 6 HP and leveled up to 7")

If a sub-agent's task becomes impossible (e.g., a whiteout teleports the player away), it returns `failed` with an explanation. The parent then re-plans from the new state.

### Task Definition

All tasks must be defined with a **clear, detectable goal**. The agent is responsible for recognizing when the goal is met. This is a prompting concern - tasks need to be specific enough that completion is unambiguous.

### Stuck Detection

Each task has a **max turns limit**. If an agent exceeds this limit without completing, it is forced to return `failed` with a summary to its parent agent. The parent can then spawn a new agent to retry or re-plan.

### Max Depth Enforcement

If an agent at the maximum allowed depth tries to `create_subtask`, the tool returns an error telling the agent it is at max depth and must handle the task itself directly.

### Configurable Variables

- **Max task depth:** How many levels deep the agent tree can go.
- **Max turns per task:** How many turns an agent gets before being forced to fail and return.

## Emulation (Confirmed)

**Emulator:** mGBA via Lua TCP socket server. Chosen because the first target game is Pokemon FireRed (GBA). PyBoy was considered but only supports Game Boy / Game Boy Color, not GBA.

**Protocol:** Request-response. The harness explicitly requests a screenshot when the agent is ready, processes it, sends actions, then requests the next state. No polling or timers.

**Pause during thinking (configurable):** Option to pause/freeze the emulator while the LLM is processing a turn. Enabled by default. Can be disabled for games with time-based events (not relevant for Pokemon but matters for game-agnosticism).

**Screenshot Preprocessing:**
- Upscale 3-4x (GBA native resolution is small)
- Contrast and saturation boost to help VLMs read text and identify objects

**Optional (maybe):** Grid overlay on screenshots to give the LLM a coordinate system for spatial reasoning. Concern: this may break game-agnosticism (grid size depends on the game's tile system) and doesn't make sense during menus/battles. If implemented, should be toggleable and only active during overworld navigation.

## Run Logging & Reporting (Confirmed)

### Run Folder

Each experiment run creates a dedicated folder (e.g., `runs/run_2026-04-03_14-30/`). All logs are written to continuously and incrementally - every completed step, tool call, or agent action is flushed immediately so that logs survive crashes or manual stops.

### What Gets Logged

Everything:
- All prompts sent to LLMs (system prompts, user messages, tool definitions)
- All tool calls and tool responses
- All VLM inputs and outputs
- All OCR captures
- All screenshots (the actual images)
- All turn explanations ("I saw / I thought / I did")
- The full task hierarchy and which agent is executing what at any point
- Global state snapshots
- Agent spawn/return events with summaries and statuses

### Report Generator

A `report.py` script that takes a run folder as input and generates an interactive HTML report with:
- Collapsible/expandable task tree (tasks -> sub-tasks -> sub-sub-tasks)
- For each task/agent: the input screenshots, what the agent thought, what it had in memory, what actions it took
- Full drill-down into any turn: prompts, tool calls, responses, state at that point
- Timeline view of the full run

This is for post-run analysis and debugging, not real-time.

## Snapshots (Confirmed)

A snapshot captures the full state needed to resume a run from a specific point. Snapshots are a **harness concern** - the agent is not aware of them and cannot trigger them.

### What a Snapshot Contains

- **Emulator save state** - mGBA's native save state (exact game state)
- **Agent state files** - full copy of the `state/` directory (agent's self-managed memory)
- **Metadata** - timestamp, which task just completed, notes

### Snapshot Folder Structure

```
snapshots/
  after_brock/
    emulator.state
    state/
    metadata.json
  manual_route3/
    emulator.state
    state/
    metadata.json
```

### When Snapshots Are Created

- **Manual:** Human triggers a snapshot at any time.
- **Automatic (configurable):** On task completion at a configurable depth. E.g., `auto_snapshot_depth: 0` snapshots only on top-level task completion, `1` also on first-level sub-task completions, `2` goes deeper. Set to `null` to disable auto-snapshots.

### Loading Snapshots

A run can start from a snapshot instead of from scratch. The config points to a snapshot folder. The emulator loads the save state, the agent's `state/` directory is pre-populated, and a new top-level task is assigned by the human. The task hierarchy starts fresh - only the game state and agent knowledge carry over.

## Prior Art & Landscape

- **Old School RL:** Peter Whidden's "Learning to Play Pokemon Red with Reinforcement Learning" - 50,000+ hours of training, no common sense, got stuck staring at walls.
- **PokeLLM:** Used LLM for Pokemon battles but fed text-based state data, not screenshots.
- **VLM Navigation experiments:** Moondream / LLaVA describe screen, GPT-4o decides button presses.
- **Voyager (Minecraft):** LLM-driven agent with similar architecture concepts.

## Known Challenges

- **Spatial Awareness:** VLMs are good at "there is a house" but bad at "you are 3 pixels from the door."
- **Menu Navigation:** Pokemon is 90% menus; small VLMs may struggle with tiny text for move names.
- **Short-term Memory:** Without persistent state files, the model forgets what it just saw. (Addressed by the global files approach.)

## OCR System (Confirmed)

A continuously running OCR process that captures text between and during inputs. Runs during action execution AND while the LLM is thinking. The goal is to catch everything a human would read: scrolling dialogue, temporary banners (route changes), menu text, etc.

### OCR Pipeline

1. **Capture** a screenshot every ~0.5 seconds
2. **Deduplicate** - compare against the last ~3 captures using image hash/pixel difference. Skip if essentially identical (handles static screens cheaply).
3. **Preprocess** new unique frames - upscale, binarize (pure black/white), high contrast. Makes pixel fonts readable for OCR.
4. **Run OCR** on preprocessed frames (start with Tesseract + heavy preprocessing).
5. **Merge scrolling text** - detect overlapping prefixes across consecutive OCR results and keep only the longest/most complete version. E.g., "Welcome to t" + "Welcome to the Pokemon Center" = "Welcome to the Pokemon Center".
6. **Serve** the deduplicated, merged text buffer to the agent at turn start. Always included in context (not a tool call).

### OCR Fallback Options

If local Tesseract-based OCR proves unreliable on pixel fonts, API-based OCR services (e.g., DeepSeek OCR, other cheap OCR APIs) can be used as a drop-in replacement. The OCR step in the pipeline is modular - the rest of the system only cares about the final text output.

### Notes

- OCR is supplementary to the VLM, not a replacement. The agent should function without it, just with less information.
- The OCR log lives in the run folder (not in the agent's state directory) since it is harness-generated, not agent-managed.
- This is the least game-agnostic part of the system. Preprocessing tuned for GBA pixel fonts may not work for other games. Acceptable since OCR is supplementary.

## VLM as an On-Demand Tool (Confirmed)

The VLM is not only used for the automatic structured screenshot analysis each turn. The LLM can also call the VLM as a tool to ask follow-up questions about the current screen.

Example: If the structured output says "an item to the top right", the LLM can ask the VLM for movement coordinates, and the VLM could respond with something like `2y, -2x` (2 steps up, 2 steps left) to give precise spatial info.

**In direct multimodal mode:** The `ask_vlm` tool still calls the separate VLM model (not the reasoning LLM). This means the VLM model must be configured even in direct mode if the agent wants access to this tool. Having both available is useful - the reasoning LLM sees the screenshot directly, but can still delegate specific visual questions to a cheaper/faster VLM.

## Design Decisions

- **No Critic Loop.** The agent does not verify VLM observations with a separate validation step.
- **No Action Buffer / Interrupt System.** The LLM decides actions and lives with the consequences. No batching 10-20 actions with an interrupt handler.
- **Model Agnostic.** The system must be agnostic to specific LLM/VLM models. No hardcoded model choices. Multiple models should be testable.
- **Stack:** OpenRouter for model access, Pydantic AI for the agent framework.

## Future Ideas (Not for Initial Build)

- **Self-Learned Action Macros:** The agent can define its own reusable sequences of actions. E.g., "heal in pokemon center" = a stored sequence that moves the player from outside the center, inside, talks to the nurse, heals, and walks back out. These macros are self-learned and self-defined by the agent over time, not hardcoded.

- **Learning & Memory Agent:** A separate agent that runs after a task completes. It analyses the full trace of turns to identify recurring problems, inefficiencies, or patterns - then writes helpful memories/tips into the global state that future agents can benefit from. E.g., if the agent consistently gets confused navigating using the map, the learning agent could add a memory like "when navigating to a new city, check the world atlas first rather than relying on VLM directions." This creates a feedback loop where the system improves over time without retraining.

---

*This document captures the initial brainstorm. No implementation decisions have been finalized.*
