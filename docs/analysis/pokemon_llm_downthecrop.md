# Analysis: downthecrop/pokemon-llm

**Repository:** https://github.com/downthecrop/pokemon-llm
**Stars:** 13 | **Language:** Python | **Created:** 2025-04-22 | **Last Updated:** 2026-03-13
**Description:** Pokemon Gen1, Gen2, Gen3 UI and tools for LLMs to use mGBA

---

## 1. Architecture

### Overall Structure

The project is a single-agent loop that connects an LLM (with vision) to the mGBA emulator via a Lua-based TCP socket server. The architecture is:

```
LLM (multimodal) <-> Python driver (llmdriver.py) <-> TCP socket <-> Lua script (mGBA) <-> Game
```

There is no separate vision pipeline. The system sends raw screenshots (and optionally a minimap image) directly to a multimodal LLM as base64-encoded images alongside structured game state data extracted from RAM.

### Models Supported

The project is model-agnostic in the sense that it supports multiple providers via the OpenAI-compatible API pattern:
- **OpenAI** (default: o3)
- **Google Gemini** (default: gemini-2.5-flash-preview-05-20)
- **Anthropic** (default: claude-sonnet-4-20250514) -- note: spelled "ANTHOPIC" throughout the codebase
- **Groq** (default: llama-4-maverick)
- **Together** (default: Qwen2.5-VL-72B)
- **Grok/xAI** (default: grok-3-mini)
- **Ollama** (local, default: gemma3:27b)
- **LMStudio** (local, default: gemma-3-27b)

All providers are accessed through the OpenAI Python SDK with different base URLs. There is no separate VLM -- the reasoning model itself is multimodal and receives the images directly.

### No Vision Pipeline

There is no dedicated VLM step that produces a text description of the screen. The raw screenshot (with a red grid overlay for spatial reference) and optionally a minimap image are sent directly to the LLM as base64 images. The LLM is expected to interpret the visual information itself as part of its reasoning.

---

## 2. Emulation

### Emulator: mGBA

The project uses **mGBA** (specifically development builds that support scripting autolaunch). Despite the name "mGBA" being primarily a Game Boy Advance emulator, it also supports Game Boy / Game Boy Color ROMs, which is what they use for Pokemon Red/Blue (Gen 1).

### Interface: Lua TCP Socket Server

A custom Lua script (`socketserver.lua`) runs inside mGBA and opens a TCP server on port 8888. The Python client connects to this socket and communicates via a simple text-based protocol.

**Commands supported by the Lua server:**
- **Key presses:** Single keys (`U`, `D`, `L`, `R`, `A`, `B`, `S` for Start, `s` for Select) with auto-release after 6 frames
- **Key queues:** Semicolon-separated sequences (e.g., `U;U;R;R;A;`) with 30-frame spacing between inputs. The server sends `QUEUE_COMPLETE\n` when done.
- **`CAP`:** Captures a screenshot as raw ARGB pixel data (length-prefixed binary)
- **`READRANGE <addr> <len>`:** Reads arbitrary memory ranges from the emulated system (length-prefixed binary response)
- **`STATE`:** Returns current game state as text ("battle", "menu", "dialogue", "roam") based on memory flags
- **`LOADSTATE <slot>`:** Loads a save state
- **`INPUT_DISPLAY_ON`:** Toggles an on-screen input display overlay

The Lua script also includes an input display overlay (borrowed from mGBA's built-in script) for visual debugging of what buttons are being pressed.

---

## 3. Input/Output

### Sending Inputs

The LLM outputs a JSON object with either:
1. **`{"action": "U;R;R;D;"}`** -- A semicolon-separated sequence of button presses sent directly to the Lua socket server, which queues them with 30-frame spacing (about 0.5 seconds per input at 60fps).
2. **`{"touch": "5,5"}`** -- A screen-grid coordinate that triggers a BFS pathfinding system. The player is always at grid position [4,4], so `{"touch": "5,5"}` means "move one right and one down." The pathfinder computes the actual walkable path from ROM tile data and converts it to a sequence of directional inputs.

The touch system is a notably clever feature -- it abstracts away complex multi-step navigation into a single high-level "go here" command.

### Reading the Screen

Screenshots are captured via the `CAP` command to the Lua server, which uses `emu:screenshotToImage()` to get raw ARGB pixel data. The Python side:
1. Receives the raw pixel data over TCP
2. Converts it to a PIL Image
3. Draws a red 16x16 pixel grid overlay on the screenshot (to help the LLM reason about spatial positions)
4. Optionally combines the screenshot side-by-side with a minimap image
5. Base64-encodes the result and sends it to the LLM as an `image_url` in the message

The image detail level is configurable (`"low"` or `"high"`) via the OpenAI Vision API `detail` parameter.

### Minimap System

The project generates minimaps by reading the ROM directly (not from game memory at runtime). It:
1. Loads the map data, tileset, and collision data from the ROM binary
2. Builds a walkability grid (which tiles are walkable, blocked, or special/exits)
3. Renders this as an image with white=walkable, black=blocked, orange=exits/stairs/doors, blue=player position
4. Also generates a text-based 2D minimap (`W`/`B`/`O`/`P` characters) sent as part of the JSON state

---

## 4. Game State: RAM Reading vs. Screen-Only

**This project reads heavily from game memory.** This is the single biggest philosophical divergence from our approach.

### Data extracted from RAM:

| Data | Memory Address | Method |
|------|---------------|--------|
| Party Pokemon (species, HP, level, nicknames) | 0xD163+ | Direct RAM read via READRANGE |
| Badges | 0xD356 | Direct RAM read |
| Player facing direction | 0xC109 | Direct RAM read |
| Player position (tile X/Y) | 0xD361-0xD362 | Direct RAM read |
| Current map ID | 0xD35E | Direct RAM read |
| Map width | 0xD369 | Direct RAM read |
| Game state (battle/menu/dialogue/roam) | 0xD057, 0xCC51, 0xCC50, 0xCC54 | Direct RAM read |
| Battle type | 0xD05A | Direct RAM read |

### Data from ROM (not runtime RAM):
- Map layout and tileset data
- Collision/walkability data per tile
- Species ID-to-name mappings (hardcoded in data.py)
- Location ID-to-name mappings (hardcoded enum in data.py)

### What the LLM actually receives each turn:

```json
{
  "party": [{"name": "Charmander", "level": 12, "type": "Fire", "hp": 30, "maxHp": 35, "nickname": "CHAR"}],
  "map_id": 54,
  "badges": ["Boulder"],
  "position": [7, 5],
  "facing": "up",
  "map_name": "PEWTER_GYM",
  "minimap_2d": "BBBWWWPWWO;WWWWWBWWWW;...",
  "screenshot": {"image_url": {"url": "data:image/png;base64,...", "detail": "low"}},
  "minimap": {"image_url": {"url": "data:image/png;base64,...", "detail": "low"}}
}
```

The LLM receives exact numeric HP values, precise tile coordinates, map IDs, facing direction, badge flags, and a complete walkability grid -- all from memory, not from visual interpretation.

---

## 5. Decision Making

### Single Model, Single Call

One multimodal LLM handles everything: visual interpretation, strategic reasoning, and action selection. There is no multi-model pipeline, no separate vision model, and no critic/validator.

### Prompting Strategy

The system prompt (`prompts.py`) is extremely long and detailed (~3000+ words). Key aspects:

1. **Grid-based spatial reasoning:** The LLM is told that its character is always at grid position [4,4] on the screenshot (which has a red grid overlay). It must count grid cells to determine positions of objects, NPCs, and doors.

2. **Minimap cross-referencing:** The LLM is instructed to use both the screenshot and minimap together -- verifying paths are walkable (white on minimap) before planning movement.

3. **Structured analysis via XML tags:** The LLM is asked to wrap its reasoning in `<game_analysis>` tags, then output a final JSON action. This is essentially chain-of-thought prompting with structured output.

4. **First-person immersion:** The prompt instructs the LLM to speak as if it IS the player ("I see my surroundings") rather than describing screenshots. This is a minor but interesting role-playing choice.

5. **Extensive navigation rules:** The prompt has many specific rules about coordinate systems (y is inverted), orthogonal-only NPC interaction, door alignment requirements, and stuck detection ("if the same action fails multiple times, try something else").

6. **Touch vs. Action choice:** The LLM can choose between direct button presses or the pathfinding touch system per turn, but not both.

### Output Format

The LLM outputs:
```
<game_analysis>
[Detailed reasoning about current state, goals, obstacles...]
</game_analysis>

{"action":"U;R;R;A;"}
```
or
```
{"touch":"6,3"}
```

The Python code uses regex to extract the analysis section and the final JSON action.

---

## 6. State Management / Memory

### Chat History with Periodic Summarization

The project maintains a simple chat history (list of user/assistant messages). Every `CLEANUP_WINDOW` turns (default: 10), the entire chat history is sent to the LLM for summarization. The summary replaces the chat history, and a new system prompt is constructed with the summary embedded.

The summarization prompt asks the LLM to produce:
```json
{
  "summary": "First-person narrative of recent actions (~300 words)",
  "primayGoal": "Current primary goal (2 sentences max)",
  "secondaryGoal": "Current secondary goal (2 sentences max)",
  "tertiaryGoal": "Current tertiary goal (2 sentences max)",
  "otherNotes": "Additional notes (3 sentences max)"
}
```

This summary is injected into the next system prompt as `actionSummary`, giving the LLM a compressed memory of what it has done.

### No Persistent State Files

There is no file-based state management. No saved knowledge, no inventory tracking files, no map notes. The only "memory" is the rolling chat history and its periodic summaries. When a summary happens, all detailed history is lost and replaced with the compressed version.

### WebSocket UI State

A separate `state` dictionary is maintained for the web UI (broadcasting via WebSocket), tracking actions taken, badges, team, goals, tokens used, and log entries. This is for display purposes only and does not feed back into the LLM.

---

## 7. Goal System

### No Goal Hierarchy or Task Decomposition

The project has no task system, no goal tree, and no sub-agent spawning. Goals are managed in two ways:

1. **System prompt instructions:** The base system prompt says "progress through the game." Optionally, a benchmark file can inject specific instructions (e.g., "defeat Brock").

2. **Self-reported goals from summaries:** When the chat history is summarized, the LLM extracts its own primary/secondary/tertiary goals from the summary. These are displayed in the web UI and injected back into the next system prompt cycle. However, these are purely self-reported and there is no mechanism to verify completion, spawn sub-tasks, or enforce goal hierarchies.

### Benchmark System

The project has a basic benchmark framework (`benchmark.py`) allowing custom benchmark files that define:
- `instructions`: Text injected into the system prompt
- `max_loops`: Maximum iterations before stopping
- `validation(state)`: A function that checks game state to determine if the benchmark goal is met
- `finalize(state, model)`: Called when the benchmark ends

The included `gymbench.py` benchmark tests "defeat Brock" -- it succeeds when `len(badges) > 0`. This is validation via RAM reading, not visual verification.

---

## 8. Strengths and Clever Ideas

### Touch/Pathfinding System
The most innovative feature. Instead of requiring the LLM to output 20+ individual directional inputs to navigate across a room, it can say `{"touch": "7,2"}` and the system BFS-pathfinds through the ROM's walkability data to generate the correct input sequence. This dramatically reduces the number of LLM calls needed for navigation and eliminates a huge category of navigation errors (walking into walls, getting stuck on geometry).

### Grid Overlay on Screenshots
Drawing a 16x16 pixel grid on the screenshot and telling the LLM "you are at [4,4]" gives the model a concrete coordinate system to reason with. This is a simple but effective way to help VLMs with spatial reasoning, which they notoriously struggle with.

### 2D Text Minimap
Alongside the visual minimap image, the system also sends a text-based minimap (`WWBWWWPWWBO...`) that the LLM can reason about without relying on vision. This redundancy -- both visual and textual representations of walkability -- is smart for robustness.

### ROM-based Map Analysis
Rather than trying to understand map layout from the screen at runtime, the system pre-analyzes the ROM's tile data to build accurate walkability grids. This is deterministic and correct, unlike VLM-based map interpretation.

### Multi-Provider Support
Supporting 8+ LLM providers through the OpenAI-compatible API pattern makes benchmarking different models straightforward.

### Benchmark Framework
The ability to define specific benchmarks with custom instructions, loop limits, and validation functions enables reproducible testing -- important for comparing models and approaches.

### Streaming with Timeout Handling
The LLM driver has sophisticated timeout handling: first-chunk timeouts for streaming, total timeout for the full response, and special non-streaming paths for reasoning models that need longer "thinking" time.

---

## 9. Weaknesses and Limitations

### Heavy RAM Dependency
The entire system is built on reading game memory. Position, HP, badges, party composition, map ID, facing direction, game state -- all come from specific memory addresses. This means:
- **Hardcoded to Pokemon Red/Blue Gen 1.** Different games (even Gen 2/3 as planned) would need entirely different memory address maps.
- **Brittle.** Any ROM hack or different version would break the memory reads.
- **Not transferable.** The approach cannot generalize to other games without reverse-engineering their memory layouts.

### No Persistent Memory
The rolling summary system loses information aggressively. After summarization, the LLM has only a ~300-word compressed narrative of everything it has done. There is no way to store and recall specific knowledge (e.g., "there is a hidden item at Route 3, position X,Y" or "I already tried to catch the Snorlax and failed"). Long-term learning is impossible with this architecture.

### No Task Decomposition
Without a goal hierarchy, the LLM must hold its entire strategic plan in its working context. For a complex game like Pokemon with dozens of sequential objectives, this single-agent flat approach will likely degrade as the game progresses and the context window fills up.

### Enormous System Prompt
The system prompt is extremely long and contains dozens of specific rules about navigation, coordinate systems, menu handling, and more. This:
- Consumes a large portion of the context window every turn
- May cause the LLM to fixate on prompt rules rather than actual game state
- Is difficult to maintain and debug
- Contains repetitive/contradictory instructions in places

### No OCR
There is no text extraction from the game screen. The LLM must visually read all in-game text (dialogue, menus, move names, item names) from the screenshot. At `"detail": "low"`, this is extremely challenging -- Game Boy text is tiny and pixelated.

### Single-Turn Reasoning Only
Each turn is a single LLM call. The LLM cannot ask follow-up questions about the screen, request additional information, or perform multi-step reasoning within a turn. It sees the state, thinks once, and acts.

### No Stuck Detection Beyond Prompt Instructions
The only stuck detection is a prompt instruction telling the LLM "if the same action fails multiple times, try something else." There is no programmatic detection of the agent being stuck (e.g., no position change for N turns), no automatic recovery, and no escalation mechanism.

### Fixed Timing
The loop runs on a fixed interval (13 seconds between turns, minimum 10 seconds). This is not adaptive to the game state -- simple actions (pressing A in a menu) get the same time as complex navigation decisions.

### No Battle Intelligence
Despite reading party and badge data from RAM, there is no battle-specific logic. The LLM must figure out battle mechanics, type matchups, move selection, and item usage entirely from the screenshot and its training knowledge. There is no structured battle state extraction (enemy HP, enemy species, available moves with PP, etc.).

---

## 10. Comparison to Our Approach

### Where They Align

| Aspect | Their Approach | Our Approach | Alignment |
|--------|---------------|--------------|-----------|
| Multimodal LLM as decision maker | Yes | Yes | Aligned |
| Model agnostic | Yes (8 providers) | Yes (OpenRouter) | Aligned |
| Screenshot-based visual input | Yes | Yes | Aligned |
| Agent manages its own goals (partially) | Self-reported goals in summaries | Agent manages state files | Partially aligned |

### Where They Diverge

| Aspect | Their Approach | Our Approach | Winner |
|--------|---------------|--------------|--------|
| **Information source** | Heavy RAM reading (exact HP, coordinates, map IDs, badges from memory) | Human-level information only (screen + OCR) | **Ours** is harder but more principled, generalizable, and honest about the challenge |
| **Vision pipeline** | None -- raw screenshots to LLM | Configurable separate VLM mode or direct multimodal | **Ours** is more flexible |
| **Persistent state** | Rolling summary only (~300 words), no files | Agent-managed state directory with _hide system | **Ours** is far richer |
| **Task system** | None -- flat single-agent | Recursive task decomposition with sub-agent spawning | **Ours** is more sophisticated |
| **Context management** | Summarize-and-reset every 10 turns | Turn explanations + context cleanup per turn | **Ours** preserves more useful information |
| **OCR** | None | Continuous OCR process | **Ours** supplements vision effectively |
| **VLM as tool** | Not available | LLM can call VLM for follow-up questions | **Ours** enables interactive visual reasoning |
| **Game agnosticism** | Hardcoded to Pokemon Red memory addresses | Game-agnostic by design | **Ours** is fundamentally more portable |
| **Navigation** | BFS pathfinding from ROM tile data | Agent must navigate from visual information | **Theirs** is more effective but relies on ROM data |
| **Spatial awareness** | Grid overlay + exact coordinates from RAM | Must be solved through VLM/OCR | **Theirs** cheats but gets results; ours faces the hard problem honestly |
| **Stuck detection** | Prompt-based only | Max turns per task with forced failure and re-planning | **Ours** has programmatic safeguards |

### Key Takeaways for Our Project

**Ideas worth borrowing:**
1. **Grid overlay on screenshots.** Drawing a grid on the screenshot to give the LLM a spatial coordinate system is simple and effective. We should consider this even without RAM-based coordinates -- it helps VLMs reason about "how many tiles away" something is.
2. **mGBA + Lua socket server pattern.** Their emulator integration is clean and well-designed. The TCP socket approach with a Lua script is a proven pattern we could adopt or adapt.
3. **Touch/pathfinding as a tool.** While their implementation reads ROM data (which violates our philosophy), the concept of giving the LLM a "navigate to this screen position" tool that handles pathfinding is powerful. We could potentially implement a simpler version that uses visual obstacle detection instead of ROM tile data.
4. **Benchmark framework.** Having reproducible benchmarks with specific instructions and validation functions is valuable for comparing approaches. We should build something similar.
5. **Multi-provider support via OpenAI-compatible API.** Their pattern of using the OpenAI SDK with different base URLs is clean. We are using OpenRouter which solves this differently but the benchmarking approach is worth noting.

**Pitfalls to avoid:**
1. **RAM reading dependency.** Their entire system would collapse without memory reads. Our "human-level only" philosophy is harder but produces a more honest and generalizable system.
2. **Flat agent with no task system.** Their single-agent loop with no task decomposition will likely struggle with complex multi-step objectives. Our recursive task system is better designed for this.
3. **Aggressive memory loss.** Summarizing to 300 words every 10 turns loses too much information. Our state file approach with the `_hide` system is much richer.
4. **Massive monolithic system prompt.** Their prompt tries to encode every possible navigation rule in one giant blob. Our approach of game-specific knowledge in prompts + agent-managed state should be more maintainable.
5. **No multi-step reasoning per turn.** Their single LLM call per turn with no tool use is limiting. Our design allowing multiple steps (read files, ask VLM follow-ups, edit state) within a turn is more powerful.

### Bottom Line

The downthecrop/pokemon-llm project is a solid, functional implementation that achieves playable results by leaning heavily on game memory reads and ROM analysis. Its core innovation -- the pathfinding touch system -- is genuinely clever. However, its reliance on RAM reading makes it fundamentally a different kind of project than ours. They are building "an LLM that plays Pokemon with privileged access to game internals." We are building "an LLM that plays Pokemon like a human would." These are different challenges with different design constraints, and our approach is more ambitious, more generalizable, and more intellectually interesting -- even if it will be harder to achieve comparable results in the short term.
