# Analysis: PokemonLLMAgentBenchmark

**Repository:** https://github.com/CalebDeLeeuwMisfits/PokemonLLMAgentBenchmark
**Authors:** Misfits and Machines (https://misfitsandmachines.com/)
**Analyzed:** 2026-04-02
**Inspiration cited:** Anthropic's ClaudePlaysPokemon stream

---

## 1. Architecture

The project uses a straightforward four-file architecture:

- **`main.py`** -- Entry point and game loop orchestrator. Initializes the emulator, controller, screen capture, knowledge base, and agent. Runs a continuous loop: capture screenshot, feed to agent, agent acts, repeat with a 0.5s sleep between iterations.
- **`agent.py`** -- Core intelligence. Contains the `KnowledgeBase` class, `PokemonAgent` class, and all tool functions decorated with `@tool` for the smolagents framework.
- **`game_interface.py`** -- Emulator wrapper. Contains `Emulator`, `Controller`, `ScreenCapture`, and `PokemonRedMemoryMap` classes for interfacing with PyBoy.
- **`dataset_manager.py`** -- Collects gameplay data (screenshots + reasoning + actions) and pushes to Hugging Face Hub for future benchmarking.

**Models supported:**
- Anthropic Claude (default: `claude-3-sonnet-20240229`, also mentions Claude 3.7 Sonnet)
- Ollama local models (default: DeepSeek-Coder-V2-Lite-Base-GGUF:Q6_K, also mentions deepseek-coder:16b-instruct)

**Agent framework:** Hugging Face `smolagents` library, using their `CodeAgent` class. The CodeAgent follows a thought-action-observation loop and has a planning step every 3 actions.

**No vision pipeline.** There is no VLM component. Screenshots are passed to the agent as an `additional_args` parameter, but the code does not show any actual multimodal processing of the screenshot image by the LLM. The agent appears to rely primarily on OCR (pytesseract) and memory reads rather than visual understanding of the screen.

---

## 2. Emulation

**Emulator:** PyBoy (https://github.com/Baekalfen/PyBoy), a Python-native Game Boy emulator.

**Key characteristics:**
- Initialized with `PyBoy(rom_path, window_type="SDL2", game_wrapper=True)`
- Runs at normal speed by default (configurable to unlimited for training)
- Supports frame skipping: `pyboy.tick(count, render)` to advance multiple frames
- Direct Python API -- no subprocess or external emulator process needed
- Save state support mentioned in README but not implemented in code

**Interface:** The emulator is wrapped in an `Emulator` class that provides high-level methods like `send_input()`, `read_memory()`, `get_player_position()`, `get_pokemon_party()`, and `has_badge()`. The `Controller` class wraps button-press logic with sequencing and delays. `ScreenCapture` captures frames via PyBoy's `screen.image` API.

---

## 3. Input/Output

### Input (to game)
Button presses are sent via PyBoy's native `pyboy.button(button_name)` API, followed by `pyboy.tick()` to process the frame. The `Controller` class supports:
- Individual button presses with validation
- Sequential button sequences with 0.1s delays between presses
- A `wait` command that just ticks a frame
- A `navigate_to(x, y)` method that calculates a naive rectilinear path (move horizontally then vertically)

Valid buttons: UP, DOWN, LEFT, RIGHT, A, B, START, SELECT.

### Output (from game)
Two mechanisms:
1. **Screenshots:** Captured via `pyboy.screen.image` (PIL Image), converted to numpy arrays via OpenCV. Resolution is 160x144 (native Game Boy).
2. **OCR:** Pytesseract is used to extract text from screenshots. Supports region-based extraction and dialog box detection (via edge detection + Hough line transforms on the bottom third of the screen).
3. **Direct memory reads:** The primary mechanism for understanding game state (see section 4).

---

## 4. Game State: Memory Reads vs. Screen Only

**This project heavily relies on direct memory/RAM reads.** This is the single biggest philosophical divergence from our approach.

The `PokemonRedMemoryMap` class defines hardcoded memory addresses for Pokemon Red:

| Data | Memory Address | Method |
|------|---------------|--------|
| Player X position | `0xD362` | `get_player_position()` |
| Player Y position | `0xD361` | `get_player_position()` |
| Player direction | `0xD368` | `get_player_position()` |
| Current map ID | `0xD35E` | `get_player_position()` |
| Party count | `0xD163` | `get_pokemon_party()` |
| Party species | `0xD164` | `get_pokemon_party()` |
| Party data | `0xD16B` | `get_pokemon_party()` |
| Battle type | `0xD057` | Available but not yet tooled |
| Enemy species | `0xD0B5` | Available but not yet tooled |
| Enemy level | `0xD127` | Available but not yet tooled |
| Badge flags | `0xD356` | `has_badge()` |
| Event flags | `0xD747` | Available but not yet tooled |
| Item flags | `0xD31D` | Available but not yet tooled |

All of these are exposed as LLM-callable tools (`get_player_position`, `get_pokemon_party`, `has_badge`). The agent can query exact coordinates, map IDs, party composition, and badge status directly from RAM at any time.

The README explicitly advocates for more memory mapping: "Track game progress through memory flags", "Detect battles through memory rather than image analysis", "Access precise Pokemon stats and moves", "Monitor inventory items and map location."

**Comparison to our philosophy:** This directly violates our "human-level information only" principle. A human player cannot read RAM addresses -- they see the screen and nothing else. This project treats memory access as a feature; we treat it as cheating.

---

## 5. Decision Making

**Single model, single agent.** The `CodeAgent` from smolagents handles all reasoning. There is no multi-model setup, no separate vision model, no critic, and no task decomposition.

**Prompting strategy:** Minimal. Each turn, the agent receives:
```
Analyze the current game state and take the next logical action to make progress.

Current knowledge base:
[full knowledge base dump]

Use the tools available to you to interact with the Pokemon game and make progress.
```

The smolagents `CodeAgent` follows a thought-action-observation loop internally:
1. Think about what to do
2. Generate Python code that calls the available tools
3. Observe the result
4. Repeat (up to `max_steps=10` per turn)

Planning occurs every 3 steps (`planning_interval=3`).

**No game-specific strategic prompting.** There is no guidance on Pokemon battle strategy, navigation approaches, menu interaction patterns, or progression priorities. The prompt is generic and relies entirely on the LLM's pre-existing Pokemon knowledge.

---

## 6. State Management

### Knowledge Base
The `KnowledgeBase` class is a simple dictionary of string sections:
- `game_controls` -- static text about button mappings
- `locations` -- current location (initialized to "Pallet Town")
- `pokemon_team` -- party info (initialized to "No Pokemon in team yet")
- `current_objective` -- current goal (initialized to "Start the game and choose a starter Pokemon")
- `map_knowledge` -- map info (initialized with Pallet Town description)

The agent can update any section via the `update_knowledge` tool. The entire knowledge base is dumped into the prompt every turn.

**No hide/surface mechanism.** All knowledge base content is always included in the prompt. As the knowledge base grows, this will consume increasing context window space.

**Persistence:** Can be saved/loaded to JSON files via CLI flags (`--save-knowledge`, `--load-knowledge`).

### Context Management
There is no turn-based context management. The smolagents framework handles its own internal conversation state within a run, but there is no explicit mechanism for:
- Summarizing previous turns
- Compressing history
- Selective memory retrieval

Each call to `agent.run()` appears to be independent -- the smolagents CodeAgent may or may not maintain internal state between runs (depends on framework implementation).

---

## 7. Goal System

**No goal/task hierarchy.** There is a single `current_objective` field in the knowledge base, initialized to "Start the game and choose a starter Pokemon." The agent can update this field, but there is:
- No task decomposition
- No sub-task spawning
- No parent-child task relationships
- No completion detection
- No stuck detection / max turn limits
- No recursive planning

The hardcoded prompt every turn is simply "Analyze the current game state and take the next logical action to make progress." There is no mechanism for the agent to set its own goals, break down complex objectives, or track progress toward milestones.

---

## 8. Strengths and Clever Ideas

### Dataset Collection Pipeline
The `DatasetManager` is genuinely useful. It captures (screenshot, game_state, reasoning, action, result) tuples and pushes them to Hugging Face Hub. This creates a reusable dataset for:
- Training future models on gameplay decisions
- Analyzing agent behavior patterns
- Benchmarking different LLM agents
This is infrastructure we should consider building.

### smolagents CodeAgent
Using smolagents' CodeAgent means the LLM generates Python code to call tools rather than using a rigid action format. This gives it flexibility to compose tool calls, use conditionals, and do light computation. The thought-action-observation loop with periodic planning (every 3 steps) is a reasonable pattern.

### PyBoy as Emulator
PyBoy is a strong choice: pure Python, direct API access, no subprocess management, native screenshot capture, frame control, and speed manipulation. It is the same emulator we would likely want to use.

### Navigate-To Helper
The `Controller.navigate_to(x, y, current_x, current_y)` method provides naive pathfinding (move horizontally then vertically). While simplistic, it shows awareness that navigation is a core challenge.

### Dialog Box Detection
Using OpenCV edge detection + Hough line transforms to detect dialog boxes is a practical heuristic. It is brittle but directionally useful.

### Visualization Manager
The threaded `VisualizationManager` that displays agent thoughts in real-time is good for development and debugging.

---

## 9. Weaknesses and Limitations

### No Actual Vision
Despite capturing screenshots and passing them to the agent, there is **no evidence the LLM actually processes the images visually**. The screenshot is passed as `additional_args={"screenshot": screenshot}` to `agent.run()`, but:
- There is no multimodal model configuration
- The default models (Claude 3 Sonnet, DeepSeek Coder) are text models in this context
- No base64 encoding or image-to-text conversion happens before the agent call
- The agent relies on OCR and memory reads instead

This means the "screenshot analysis" described in the README is largely aspirational. The agent is effectively blind to the screen's visual content.

### Heavy Dependence on Memory Reads
The agent's primary interface with the game is through RAM, not through perception. This makes it:
- Completely game-specific (hardcoded Pokemon Red memory addresses)
- Unable to generalize to other games
- Not a test of visual game understanding
- More of a "memory-reading bot with LLM reasoning" than an "LLM playing a game"

### No Context Management
Every turn is essentially independent. There is no mechanism to remember what happened in previous turns beyond the knowledge base sections. The agent has no sense of narrative continuity or action history.

### Minimal Prompting
The generic prompt "Analyze the current game state and take the next logical action" provides almost no guidance. There is no:
- System prompt with game knowledge
- Strategy guidance for battles, navigation, or menus
- State machine or phase detection
- Error recovery instructions

### Bugs in Code
- The Ollama wrapper has a typo: `self.self.temperature` (double self) in the string prompt handler
- The `pokemon_tools.py` file is noted as deprecated but still present
- The `get_pokemon_name` method only handles 5 Pokemon names (Bulbasaur, Charmander, Squirtle, Pikachu, Eevee)

### No Stuck Detection
If the agent loops doing the same thing or gets trapped, there is no mechanism to detect or recover from this. No turn limits, no progress tracking, no escalation.

### Single Agent
No task decomposition means the same agent context must handle everything from "walk to the next city" to "fight this battle" to "navigate this menu." This leads to context pollution and makes it hard for the agent to focus.

### Limited OCR
Pytesseract on 160x144 Game Boy screenshots with pixel fonts is unreliable. The screenshots need significant preprocessing, and even then, the custom Pokemon font will produce many OCR errors. The dialog box detection heuristic is fragile.

---

## 10. Comparison to Our Approach

### Where They Align

| Aspect | Their Approach | Our Approach | Alignment |
|--------|---------------|--------------|-----------|
| Emulator | PyBoy (Python native) | Likely PyBoy | Strong |
| LLM-driven decisions | Yes | Yes | Strong |
| Knowledge base | Dictionary of sections | Free-form state files | Conceptually similar |
| Model flexibility | Anthropic + Ollama | OpenRouter (model agnostic) | Both support multiple models |
| Screenshot capture | PyBoy screen API | PyBoy screen API (likely) | Strong |
| OCR | Pytesseract | Continuous OCR system | Both use OCR |

### Where They Diverge

| Aspect | Their Approach | Our Approach | Winner |
|--------|---------------|--------------|--------|
| **Information source** | RAM memory reads + OCR | Screen only (human-level) | Ours (principled) |
| **Vision** | No real vision pipeline | Configurable VLM / direct multimodal | Ours (actually uses vision) |
| **Game specificity** | Hardcoded Pokemon Red memory map | Game-agnostic harness | Ours (generalizable) |
| **Task system** | None (single flat loop) | Recursive task decomposition with sub-agents | Ours (far more sophisticated) |
| **Context management** | None (independent turns) | Turn explanations, context cleanup, history compression | Ours (explicit design) |
| **State management** | Flat dictionary, always fully included | `_hide` system, agent-controlled structure | Ours (more scalable) |
| **Stuck detection** | None | Max turns per task, forced failure, parent re-planning | Ours (built-in) |
| **Prompting** | Generic one-liner | System prompts with game guidance, structured output | Ours (more directed) |
| **Agent framework** | smolagents CodeAgent | Pydantic AI | Different choices, both reasonable |
| **Dataset collection** | HuggingFace Hub pipeline | Not planned (yet) | Theirs (useful idea) |

### Key Philosophical Differences

1. **Human-level information only:** This is the core divergence. Their project treats memory reads as a feature and recommends expanding them. Our project explicitly rejects this. Their agent "knows" its exact coordinates, map ID, party composition, and badge status from RAM. Our agent must figure all of this out from looking at the screen, just like a human would. This makes our project fundamentally harder but also fundamentally more interesting as a test of LLM game-playing capability.

2. **Game-agnostic vs. game-specific:** Their `PokemonRedMemoryMap` with hardcoded addresses makes the system useless for any other game. Our architecture is designed so that game-specific knowledge lives in prompts and agent-managed state, not in harness code.

3. **Agent manages its own knowledge:** Their knowledge base has predefined sections initialized with Pokemon-specific content. Our agent decides what to remember and how to structure it. Their approach is simpler but less flexible; ours trusts the agent more.

4. **Task decomposition:** Their single flat loop with "take the next logical action" will struggle with complex multi-step objectives. Our recursive task system with sub-agents is designed to handle the full game.

### What We Should Learn From Them

1. **Dataset collection to HuggingFace Hub.** Their `DatasetManager` pattern of recording (screenshot, reasoning, action, result) tuples and pushing to HF Hub is genuinely useful. We should consider building similar infrastructure for analyzing agent behavior and potentially training future models.

2. **PyBoy integration patterns.** Their `Emulator`, `Controller`, and `ScreenCapture` classes provide a clean reference for PyBoy integration. The button mapping, frame ticking, and screenshot capture code is practical and reusable.

3. **Dialog box detection heuristic.** While fragile, their OpenCV-based dialog detection (edge detection + Hough lines on the bottom third) is a useful starting heuristic for our OCR system to know when dialog text is present.

4. **smolagents CodeAgent pattern.** The thought-action-observation loop with periodic planning is a validated pattern, even if we use a different framework. The idea of the LLM generating code to compose tool calls (rather than rigid action schemas) is worth considering.

5. **Emulation speed control.** Their ability to set `emulation_speed(0)` for unlimited speed and `tick(count, False)` for frame skipping without rendering is important for training and testing efficiency.

---

## Summary

PokemonLLMAgentBenchmark is an early-stage project that connects an LLM to Pokemon Red via PyBoy. Its core approach relies heavily on reading game memory for state information, with minimal actual vision processing. The architecture is simple -- a single agent in a flat loop with a basic knowledge base and generic prompting.

Our project is significantly more ambitious in design: human-level information only, game-agnostic architecture, recursive task decomposition, sophisticated state management, and a real vision pipeline. Their project validates that PyBoy is the right emulator choice and provides useful reference code for emulator integration and dataset collection. However, their fundamental approach of reading RAM for game state is the opposite of what we are building.

**Relevance to our project: Medium.** Useful as a reference for PyBoy integration mechanics and as a cautionary example of what happens without a vision pipeline or task system. The dataset collection pattern is worth adopting. The core architecture and philosophy are fundamentally different from ours.
