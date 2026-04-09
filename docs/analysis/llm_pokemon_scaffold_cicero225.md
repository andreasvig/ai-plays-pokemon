# Analysis: cicero225/llm_pokemon_scaffold

**Repository:** https://github.com/cicero225/llm_pokemon_scaffold
**License:** GPLv3
**Language:** Python
**Based on:** Anthropic's starter code (David Hershey)
**Analysis date:** 2026-04-02

---

## 1. Architecture

### Overall Structure

The project is a single-agent loop built around a `SimpleAgent` class (~1,850 lines) that orchestrates everything. There is no sub-agent hierarchy or task decomposition system. The architecture is flat: one agent runs in a loop, taking screenshots, sending them to an LLM with game state data, receiving tool calls, executing them, and repeating.

Key files:
- `agent/simple_agent.py` - Monolithic agent class with all logic (102KB)
- `agent/emulator.py` - PyBoy wrapper with threading, pathfinding, collision detection
- `agent/memory_reader.py` - Reads Pokemon Red RAM addresses for full game state
- `agent/prompts.py` - Large system prompts for different models and sub-tasks
- `agent/tool_definitions.py` - Tool schemas in Anthropic, Google, and OpenAI formats
- `agent/utils.py` - Cross-model message format conversion utilities
- `config.py` - Model selection and parameters

### Models Supported

- Claude 3.7 Sonnet (primary, default)
- Gemini 2.5 Pro/Flash
- OpenAI o3/o4-mini
- Any model is selectable via `config.py` (one active at a time)

The code directly instantiates API clients for Anthropic, Google GenAI, and OpenAI. There is no abstraction layer or router like OpenRouter -- each provider has bespoke handling code scattered throughout the agent.

### Vision Pipeline

There is **no separate VLM pipeline**. The reasoning LLM receives screenshots directly as base64-encoded images alongside text data. Screenshots are:
- Captured from PyBoy's screen buffer (`pyboy.screen.ndarray`)
- Upscaled 4x (from 160x144 to 640x576)
- Annotated with a red grid overlay dividing the screen into a 10x9 tile grid
- Each tile is labeled with: absolute coordinates, passability ("IMPASSABLE"), exploration status ("EXPLORED", "RECENTLY VISITED", "CHECK HERE"), NPC presence ("NPC/OBJECT"), and any user-assigned labels

This annotated screenshot plus a text-based ASCII collision map plus full RAM state data are all sent to the LLM each turn.

---

## 2. Emulation

### Emulator

**PyBoy** -- a Python-native Game Boy emulator. Requires Python 3.11 specifically (newer versions may break compatibility).

### Interface

The emulator runs in a **separate thread** with a custom `PriorityLock` synchronization mechanism. This is necessary because "PyBoy just doesn't work unless it's ticking and receiving button presses on the same thread" as initialization.

Key design: the emulator keeps running while the LLM is thinking, rather than pausing. This prevents the game from freezing during API calls.

### Pathfinding

The emulator includes a built-in **A* pathfinder** that operates on a downsampled 9x10 collision map. It accounts for terrain passability, sprite positions, and tile-pair collision restrictions (certain tile transitions are blocked in specific tilesets). This enables the `navigate_to` tool to automatically move the player to on-screen coordinates without the LLM needing to issue individual directional presses.

---

## 3. Input/Output

### Sending Inputs

Button presses are queued via a `button_queue`. Each press is held for **10 frames**, released, then the system waits **120 frames** before the next input. Special commands include "wait" (skip frames), "load_state", "save_state", and "stop".

The LLM has access to these tools:
- `press_buttons` -- send a sequence of Game Boy buttons (a, b, start, select, up, down, left, right)
- `navigate_to` -- auto-pathfind to an on-screen tile coordinate
- `navigate_to_offscreen_coordinate` -- navigate to a coordinate on the expanded collision map (either via direct pathfinding or by asking another LLM call to plan the route)
- `bookmark_location_or_overwrite_label` -- label a tile with descriptive text
- `mark_checkpoint` -- log an achievement and reset step counter
- `detailed_navigator` -- invoke a sub-model for maze navigation assistance

### Reading the Screen

The LLM receives:
1. An annotated screenshot (base64 PNG) with grid overlay and tile labels
2. A text-based ASCII collision map showing the explored area with distance-to-tile numbers
3. Full game state from RAM (see section 4)
4. Location history, checkpoint history, labeled locations

---

## 4. Game State: RAM Reading (Major Philosophical Difference)

This is the **biggest divergence from our approach**. The project reads extensively from Pokemon Red's RAM using hardcoded memory addresses. The `memory_reader.py` file (34KB) reads:

- **Player name** (0xD158)
- **Rival name** (0xD34A)
- **Money** (0xD347, BCD-encoded)
- **Current location** (0xD35E, mapped to named locations like "PALLET_TOWN")
- **Exact coordinates** (X at 0xD362, Y at 0xD361)
- **Badges** (0xD356, bitflags)
- **Full inventory** with item names and quantities (0xD31D+)
- **Full party data**: species, level, HP (current/max), status conditions, types, all 4 moves with PP, trainer ID, nickname (addresses 0xD16B-0xD2EC)
- **In-combat flag** (0xD057)
- **Pokedex caught count** (0xD2F7-0xD309)
- **Game time** (hours/minutes/seconds)
- **Coins** (game corner currency)
- **Warp data** (door/stair destinations)
- **Tileset ID** (for collision rule lookup)
- **Dialog text** (from tilemap buffer 0xC3A0-0xC507)
- **Collision map** (from PyBoy's game_area_collision)

All of this data is provided to the LLM as text every single turn. The system prompts explicitly rank RAM data as "100% reliable" and conversation history as "unreliable."

This means the LLM never needs to:
- Read its own HP from the screen
- Figure out what moves it knows
- Determine its location by looking at the environment
- Parse menu text
- Track inventory by memory

**The LLM is essentially playing with a strategy guide HUD that gives it perfect information at all times.** The vision component (screenshot) is primarily used for spatial navigation and NPC identification, not for understanding game state.

---

## 5. Decision Making

### Single Model, Single Agent

One LLM makes all decisions. There is no multi-model reasoning pipeline for gameplay. The model receives the full context (screenshot + RAM state + collision map + history) and responds with tool calls.

### Prompting Strategy

The system prompts are extensive (~38KB across all variants) and highly Pokemon-specific. Key strategies:

- **Depth-first search emphasis**: The prompts repeatedly instruct the model to explore unexplored tiles before revisiting known areas
- **Thinking tags**: The model is required to use `<thinking>` tags and explain its reasoning before acting
- **Coordinate-based navigation**: Heavy emphasis on using the text-based map and coordinate system rather than visual interpretation
- **Model-specific prompts**: Separate system prompts for Claude, Gemini, and OpenAI, each tuned to the model's strengths (e.g., OpenAI/o3 gets simpler instructions)

### Navigator Sub-Mode

A special "detailed navigator mode" can be activated for maze-like areas. This uses a separate message history and restricted tool set (no checkpoints, no self-referential navigator calls). It is still the same model, just with different prompts and context.

---

## 6. State Management

### No Persistent File-Based State

Unlike our approach, there is no `state/` directory with agent-managed files. The README explicitly notes: "NOT included: Memory file management system (unlike ClaudePlaysPokemon)."

### What Persists

State is maintained in Python object attributes on `SimpleAgent`:
- `message_history` -- conversation messages (truncated at `max_history`, default 60)
- `location_history` -- last 40 (location, coordinate) pairs
- `label_archive` -- user-labeled tile coordinates per location
- `location_tracker` -- boolean grid of visited tiles per location
- `full_collision_map` -- expanding `LocationCollisionMap` objects per location
- `checkpoints` -- list of achievement strings
- `all_visited_locations` -- set of location names ever visited
- `location_milestones` -- (location, step_count) pairs

### Save/Load

State is serialized via **pickle** -- the code acknowledges this "has grown into a list of like 17 pickle dumps. It got out of hand." The save state is split into:
- `save.state` -- PyBoy emulator state
- `locations.pkl` -- all agent state (label archive, collision maps, location tracker, checkpoints, milestones, etc.)

---

## 7. Goal System

### No Formal Goal/Task System

There is no task hierarchy, goal decomposition, sub-agent spawning, or completion detection. The agent simply runs for a configurable number of steps (`--steps N`).

The closest thing to goals:
- **Checkpoints**: The LLM can call `mark_checkpoint` with an achievement string, which resets a step counter and logs the achievement. These are surfaced in future prompts.
- **System prompt guidance**: The system prompts tell the model what to do (explore, fight, navigate) but there is no programmatic goal tracking.

---

## 8. Strengths and Clever Ideas

### ASCII Collision Maps with Distance Fill
The expanding collision map system is genuinely clever. As the player explores, the map grows. A BFS distance fill from the player's position labels each reachable tile with its step distance. This gives the LLM a clear, text-based understanding of spatial layout that supplements the screenshot. The "StepsToReach" numbers let the LLM trace paths by following descending numbers.

### Annotated Screenshots
Overlaying coordinate labels, passability info, and exploration status directly onto the screenshot image is a smart way to bridge the gap between visual and spatial understanding. The LLM can see both the visual game world and structured navigation data in one image.

### Auto-Pathfinding (navigate_to)
The A* pathfinder built into the emulator layer means the LLM does not need to issue individual directional button presses for on-screen movement. It just says "go to tile (5, 3)" and the system handles the pathing. This dramatically reduces the number of turns needed for basic movement.

### Location Labeling
Allowing the LLM to label tiles with descriptive text ("Pokecenter entrance", "ledge", "Brock's gym") and persisting these labels across turns is a good form of agent-created spatial memory.

### Separate Emulator Thread
Running the emulator independently from the LLM reasoning thread is practical -- the game does not freeze during API calls.

### Three-Stage Summarization (Meta-Critique)
When context gets too long, the system runs a three-stage pipeline:
1. **Facts extraction**: Analyze conversation + RAM data + screenshots to deduce current game state
2. **Facts cleanup**: Remove inaccuracies, resolve contradictions, rank by source reliability
3. **Summary generation**: Produce a compressed progress summary with "IMPORTANT HINTS" if the model has been struggling

This is more sophisticated than a simple "summarize the conversation" approach.

### Exploration Tracking
The "EXPLORED" / "RECENTLY VISITED" / "CHECK HERE" tile annotations encourage depth-first exploration and help prevent the model from going in circles.

---

## 9. Weaknesses and Limitations

### Massive RAM Dependency
The entire system is built around perfect game state from memory. Remove the RAM reader and the agent would be severely crippled -- it has no robust mechanism for extracting game state from the screen alone. The system prompts rank RAM as the most reliable source and deprioritize visual information.

### Pokemon Red Hardcoded
Despite the general-seeming architecture, the project is deeply hardcoded to Pokemon Red. Memory addresses, tileset IDs, collision rules, character encoding tables, species/move/item enums, location IDs -- all are Pokemon Red specific. Porting to another game would require rewriting `memory_reader.py` entirely and adjusting large portions of the emulator and agent code.

### Monolithic Codebase
The `SimpleAgent` class is ~1,850 lines in a single file. The developer acknowledges "this was originally a pretty small state and that it got out of hand." State management, model interaction, tool processing, navigation, summarization, and display are all mixed together with no clear separation of concerns.

### No Task Decomposition
Without a goal hierarchy, the agent has no way to break down complex objectives into manageable sub-tasks. It just keeps running turns until the step limit. There is no concept of "I need to beat Brock, so first I need to level up, so first I need to find wild Pokemon."

### No Context Cleanup Between Turns
The full conversation history accumulates until hitting `max_history` (default 60 messages), at which point the entire history is summarized and replaced. There is no per-turn compression of reasoning traces, tool call results, or intermediate data. This means token costs escalate rapidly.

### Model-Specific Code Branching
The agent has extensive `if MODEL == "CLAUDE" / elif MODEL == "GEMINI" / elif MODEL == "OPENAI"` branching throughout. Adding a new model provider requires touching many places. Message format conversion is handled by utility functions but tool call parsing and response handling are scattered.

### No OCR System
There is no dedicated OCR pipeline. Dialog is read from RAM (tilemap buffer), not from the screen. This works for Pokemon Red but is not generalizable.

### No Learning or Improvement Loop
Each run starts fresh (aside from loaded save states). There is no mechanism for the agent to learn from past failures, build up strategies over time, or improve its approach based on experience.

---

## 10. Comparison to Our Approach

### Core Philosophy Alignment

| Aspect | Our Project | cicero225/llm_pokemon_scaffold |
|--------|-------------|-------------------------------|
| **Information source** | Screen only (human-level) | RAM + Screen (omniscient) |
| **Game agnosticism** | Core philosophy | Deeply Pokemon Red-specific |
| **Agent state management** | Agent-controlled free-form files | Hardcoded Python attributes |
| **Task system** | Recursive decomposition with sub-agents | None (flat step loop) |
| **Vision pipeline** | Configurable (separate VLM or direct multimodal) | Direct multimodal only |
| **OCR** | Dedicated continuous OCR process | None (reads dialog from RAM) |
| **Context management** | Per-turn cleanup, only explanations persist | Accumulate until summarization threshold |
| **Goal setting** | Human-set top-level, recursive decomposition | No formal goals |
| **Model support** | OpenRouter (any model) + Pydantic AI | Direct API clients (Anthropic, Google, OpenAI) |
| **Learning** | Planned learning agent for post-task analysis | None |

### Where They Align

- **Screenshot as primary visual input**: Both send screenshots to the LLM for spatial understanding
- **Multimodal LLM as decision maker**: Both use the LLM to interpret the game and decide actions
- **State persistence between turns**: Both maintain some form of memory across turns
- **Turn-based loop**: Both follow a capture -> think -> act cycle

### Where They Fundamentally Differ

**1. Human-Level Information vs. Omniscient Information**

This is the sharpest difference. Their agent knows its exact coordinates, exact HP, exact inventory, exact location name, whether it is in combat, and what dialog is being displayed -- all from RAM. Our agent must figure all of this out from looking at the screen, exactly as a human would. Their approach is more reliable but less generalizable and less interesting as a research problem.

**2. Game-Specific vs. Game-Agnostic**

Their `memory_reader.py` is a 34KB file of Pokemon Red memory addresses. Their collision detection uses tileset-specific rules. Their prompts reference Pokemon-specific concepts. Our design explicitly separates game-specific knowledge into prompts and agent-managed state, keeping the harness generic.

**3. Flat Agent vs. Hierarchical Task System**

They have one agent running in a loop. We have recursive task decomposition with sub-agent spawning, parent blocking, and structured completion reporting. Their approach is simpler but cannot handle long-horizon planning. Our approach adds complexity but enables "beat the game" to decompose into a tree of manageable sub-tasks.

**4. Agent-Managed State vs. Hardcoded State**

Their state is a collection of Python attributes that the developer chose to track. Our agent decides what to remember, how to structure it, and what to hide/surface via the `_hide` system. Their approach is more efficient but rigid; ours is more flexible and allows the agent to adapt its memory to the situation.

**5. Context Management**

They accumulate full conversation history until a threshold, then run a three-stage summarization. We discard raw tool call history every turn and keep only compressed "I saw / I thought / I did" explanations. Our approach is more aggressive about context control and should scale better over long play sessions.

### What We Can Learn From Them

1. **ASCII collision maps with distance fill** -- Even with our screen-only philosophy, we could have the agent build and maintain its own text-based maps from VLM observations. The distance-fill concept is useful for navigation planning.

2. **Annotated screenshots** -- Overlaying structured information onto the screenshot before sending it to the LLM is a smart preprocessing step. We could annotate screenshots with grid lines, OCR-detected text, or VLM-identified landmarks.

3. **Auto-pathfinding as a tool** -- While we cannot use RAM-based collision data, we could potentially build a pathfinding tool that works on agent-constructed maps or VLM-interpreted terrain.

4. **Location labeling** -- Letting the agent name and annotate locations it discovers, then surfacing those labels in future turns, is a good pattern for spatial memory that fits within our architecture.

5. **Three-stage summarization** -- Their meta-critique approach (extract facts -> clean facts -> summarize) is more robust than a single-pass summary. We could adapt this for our turn explanation compression, especially when summarizing many turns of history.

6. **Exploration tracking** -- Marking tiles as explored/unexplored/recently visited helps prevent the model from going in circles. We could implement something similar using agent-managed state.

### What We Should Explicitly Avoid

1. **RAM reading** -- This is antithetical to our core philosophy. It makes the problem easier but less interesting and non-generalizable.

2. **Monolithic agent class** -- Their 1,850-line single class is hard to maintain. Our separation of concerns (harness vs. agent vs. tools vs. state) should be maintained.

3. **Model-specific branching** -- Using OpenRouter and Pydantic AI to abstract model differences is the right call. Their approach of scattering `if MODEL == X` checks everywhere is fragile.

4. **No task decomposition** -- Their flat loop cannot handle long-horizon objectives. Our recursive task system is essential for playing through an entire game.

---

## Summary

cicero225/llm_pokemon_scaffold is a practical, working implementation that makes pragmatic tradeoffs (RAM reading, Pokemon-specific code) to get results. It has genuinely clever ideas around spatial navigation (collision maps, annotated screenshots, auto-pathfinding) and context management (three-stage summarization). However, it is fundamentally a different kind of project from ours: it solves "how to get an LLM to play Pokemon Red effectively" while we are solving "how to build a general-purpose game-playing agent that operates with only human-level information." Their navigation and spatial memory techniques are worth adapting to our screen-only paradigm, but their core approach of feeding RAM data to the LLM is something we explicitly reject.
