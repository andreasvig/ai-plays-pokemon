# Analysis: martoast/LLM-Pokemon-Red

**Repository:** https://github.com/martoast/LLM-Pokemon-Red
**Analyzed:** 2026-04-02
**Status:** Early-stage project, appears to have reached the "walk around Pallet Town" phase at best.

---

## 1. Architecture

The system has three components connected in a linear pipeline:

```
mGBA Emulator (Lua script)
    <-- TCP socket -->
Python Controller (google_controller.py)
    <-- API call -->
Google Gemini (gemini-2.0-flash)
```

**Model:** Google Gemini 2.0 Flash exclusively (the README mentions OpenAI/Anthropic support was removed). The `llm_provider.py` file contains abstraction code for OpenAI and Anthropic, but `google_controller.py` hardcodes Google's Gemini client directly, bypassing the provider abstraction entirely.

**Vision pipeline:** There is no separate VLM step. The screenshot is sent directly to Gemini as a multimodal input alongside a text prompt. The image is pre-processed (3x upscale, +50% contrast, +80% saturation, +10% brightness) before being sent. This is a single-model, direct-multimodal approach.

**No multi-model setup.** One model does everything: analyze the screen, reason about what to do, and select a button.

---

## 2. Emulation

**Emulator:** mGBA (a Game Boy Advance emulator that also runs Game Boy games).

**Interface mechanism:** A Lua script (`emulator/script.lua`) runs inside mGBA's scripting environment. It communicates with the Python controller via a TCP socket on `127.0.0.1:8888`.

**Communication flow:**
1. Emulator connects to the Python controller's socket server.
2. Emulator sends `ready||true` to signal it is idle.
3. Controller sends `request_screenshot` when it wants a new frame.
4. Lua script calls `emu:screenshot(path)` to save a PNG to disk.
5. Lua script sends `screenshot_with_state||<path>||<direction>||<x>||<y>||<mapId>` back over the socket.
6. Controller processes the screenshot, gets a button decision from the LLM.
7. Controller sends a button index (0-9) over the socket.
8. Lua script calls `emu:addKey(index)` and holds it for 2 frames, then releases.
9. Lua script sends `ready||true` again. Cycle repeats.

**Key detail:** Screenshots are written to disk as PNG files and then read back by Python. This is a file-based handoff, not an in-memory transfer.

---

## 3. Input/Output

**Input to the game:** Single button presses, one at a time. The LLM chooses exactly one button per turn from: A, B, SELECT, START, UP, DOWN, LEFT, RIGHT, R, L. The button is held for 2 frames in the emulator. There is no support for button sequences or multi-press actions.

**Output from the game (what the LLM sees):**
- A single enhanced screenshot (3x upscaled with contrast/saturation/brightness adjustments).
- Game state data read from memory (direction, position, map ID) -- injected into the prompt as text.

**No OCR.** The LLM must interpret all text from the raw screenshot pixels.

**No video/frame sequences.** The LLM sees only a single static screenshot per decision.

---

## 4. Game State: Memory Reading vs. Screen Only

**This project reads from game memory.** Despite the README claiming the AI plays "by only seeing the game screen, just like a human would," the Lua script reads specific RAM addresses:

| Address  | Data             |
|----------|-----------------|
| `0xC109` | Player direction |
| `0xD362` | Player X coord   |
| `0xD361` | Player Y coord   |
| `0xD35E` | Current map ID   |

This data is sent to the Python controller and injected directly into the LLM prompt:
- "You are facing: {direction}"
- "Position: X={x}, Y={y}"
- "You are in {map_name}" (map ID is translated via a hardcoded lookup table)

Additionally, `google_controller.py` contains a `get_map_name()` function with a hardcoded dictionary mapping map IDs to location names (Pallet Town, Viridian City, Route 1, Red's House, Oak's Lab, etc.).

**Comparison to our philosophy:** This directly violates our "human-level information only" principle. A human player does not know their exact X/Y coordinates or internal map ID. The direction the character faces is arguably visible on screen, but the project reads it from RAM rather than inferring it visually. The map name lookup table is game-specific hardcoded knowledge baked into the harness, not derived from screen content.

---

## 5. Decision Making

**Single model, single turn, single button.** Each decision cycle:
1. A large prompt is assembled containing: game state (position, direction, map), recent actions history, navigation guidance text, long-term notepad content, and detailed control instructions.
2. The prompt + enhanced screenshot are sent to Gemini.
3. Gemini responds with reasoning text and tool calls.
4. The controller extracts the `press_button` tool call and sends that button to the emulator.

**Tool calling:** Gemini is given two tools:
- `press_button(button)` -- required every turn.
- `update_notepad(content)` -- optional, for updating long-term memory.

**Prompting strategy:** The prompt is heavily prescriptive. It includes:
- Explicit control descriptions ("A: To talk to people", "UP: To move your character").
- A full keyboard layout for the name entry screen (rows of letters).
- Navigation rules ("If you've pressed the same button 3+ times with no change, TRY A DIFFERENT DIRECTION").
- Warnings in all-caps ("URGENT WARNING: DO NOT PRESS A UNLESS YOU ARE ON THE CORRECT LETTER!").
- A forced instruction format ("FIRST, provide a SHORT paragraph... THEN... FINALLY use press_button").

The system prompt also uses a synthetic chat history to prime Gemini's behavior (a fake user/model exchange where the model agrees to always use the tool).

**Temperature:** 0.2 (low, favoring consistency).

---

## 6. State Management

**Short-term memory:** A deque of the last 10 actions. Each entry stores: timestamp, button pressed, full LLM reasoning text, direction, X, Y, and map ID. This is formatted and injected into the next prompt as "Short-term Memory (Recent Actions and Reasoning)."

**Long-term memory:** A single text file (`notepad.txt` / configurable path). The LLM can append to it via the `update_notepad` tool. The entire notepad content is included in every prompt. When the notepad exceeds 10KB, it is summarized by sending the full content to Gemini with a summarization prompt, and the result replaces the file.

**No structured state.** The notepad is free-form markdown. There are no separate files, no hide/show mechanism, no schema. The LLM has one flat text file to work with.

**No conversation history.** Each LLM call is stateless -- a fresh chat is created for every decision. The only continuity comes from the recent actions deque and the notepad file.

---

## 7. Goal System

**No formal goal/task system.** The notepad is initialized with some starting objectives ("Find Professor Oak to get first Pokemon", "Start Pokemon journey"), but there is no task hierarchy, no sub-task spawning, no task completion detection, and no stuck detection.

The LLM is expected to self-direct based on whatever it wrote in the notepad previously. There is no mechanism for a human to set goals at runtime, no task decomposition, and no max-turns limit.

---

## 8. Strengths and Clever Ideas

**Image enhancement pipeline.** The 3x upscale with contrast/saturation/brightness adjustments is a practical idea. Game Boy screenshots are tiny (160x144), and small text is hard for VLMs. Pre-processing the image to make details more visible is smart and low-cost.

**Request-based screenshot timing.** Rather than taking screenshots on a timer, the system uses a request-response protocol. The controller only asks for a screenshot when it is ready to process one, preventing backlog and ensuring synchronization. This is cleaner than polling.

**Notepad auto-summarization.** Using the LLM itself to compress the notepad when it gets too large is a reasonable approach to unbounded memory growth.

**Tool-calling for actions.** Forcing the LLM to use a structured `press_button` tool call rather than parsing free-text responses ("I would press A") is more reliable and avoids parsing ambiguity.

**Direction guidance text.** The `get_direction_guidance_text()` function that tells the LLM "you must be FACING an NPC to interact" is a useful piece of game-specific guidance that addresses a real failure mode.

**Name entry keyboard layout in the prompt.** Including the exact letter grid layout for name entry screens is a practical workaround for what would otherwise be an extremely difficult VLM task.

---

## 9. Weaknesses and Limitations

**One button at a time.** The LLM produces a single button press per API call. At a 3-6 second cooldown per decision, walking across a map takes an enormous amount of time and API calls. There is no action sequencing ("press RIGHT 5 times") which would drastically reduce cost and latency for routine navigation.

**Memory reading contradicts stated philosophy.** The project claims screen-only play but reads RAM for position, direction, and map ID. This is not a minor supplement -- the map name and coordinates are prominent in the prompt and likely critical to the LLM's decisions.

**No conversation continuity.** Every LLM call starts a fresh chat with a synthetic history. The model has no awareness of its own previous reasoning beyond what is crammed into the prompt. This means the 10-entry recent actions buffer and the notepad are the only threads of continuity. For complex multi-step plans, this is fragile.

**Hardcoded game knowledge.** The `get_map_name()` lookup table, the name entry keyboard layout, the control descriptions -- these are all Pokemon Red-specific. The system is not game-agnostic in any way.

**Single model bottleneck.** All reasoning, vision, and memory management go through one Gemini call. There is no separation of concerns -- the same prompt must handle screen understanding, strategic reasoning, memory management, and action selection.

**Notepad is included in full every turn.** As the notepad grows, every prompt gets longer and more expensive. The 10KB summarization threshold is quite high -- that is a lot of text to stuff into every single API call. There is no selective loading or hide/show mechanism.

**No error recovery or stuck detection.** If the LLM gets stuck pressing the same button repeatedly, there is no external mechanism to detect or correct this. The prompt includes a text hint ("if you've pressed the same button 3+ times, try a different direction") but this relies entirely on the LLM noticing and acting on it.

**Gemini-only in practice.** Despite having provider abstraction code, the actual controller hardcodes `GeminiClient`. The `llm_provider.py` abstraction is unused dead code in the main flow.

**File-based screenshot transfer.** Writing screenshots to disk and reading them back adds unnecessary I/O latency. An in-memory transfer would be more efficient, though this may be a limitation of mGBA's Lua scripting API.

**No OCR.** The LLM must read all game text from pixels. Pokemon Red has extensive text-based menus and dialogue. Without OCR as a supplement, the model must be very capable at reading small pixel fonts.

---

## 10. Comparison to Our Approach

### Where They Align With Us

| Aspect | Their Approach | Ours |
|--------|---------------|------|
| Screen as primary input | Yes (screenshot-based) | Yes (screenshot-based) |
| LLM as the decision maker | Yes | Yes |
| Some form of persistent memory | Yes (notepad file) | Yes (state directory with files) |
| Multimodal model reads screen directly | Yes (Gemini handles vision + reasoning) | Supported (direct multimodal mode) |

### Where They Differ From Us

| Aspect | Their Approach | Our Philosophy |
|--------|---------------|---------------|
| **Memory reading** | Reads RAM for position, direction, map ID | Strictly screen-only; no RAM/memory access |
| **Game-specific code** | Hardcoded map names, keyboard layouts, Pokemon-specific control hints in the harness | Game-agnostic harness; game knowledge lives in prompts and agent-managed state |
| **State management** | Single flat notepad file, fully included every turn | Structured state directory with _hide system for selective visibility |
| **Action granularity** | One button per LLM call | Button sequences per turn (e.g., "RRRRAAA") |
| **Task/goal system** | None; self-directed via notepad | Recursive task decomposition with sub-agent spawning |
| **Stuck detection** | None (only a prompt hint) | Max turns per task, forced failure and re-planning |
| **Context management** | No history; fresh chat every call; full notepad in every prompt | Turn explanations accumulated; raw history discarded; state selectively loaded |
| **Vision pipeline** | Direct multimodal only | Configurable: separate VLM or direct multimodal |
| **OCR** | None | Continuous OCR process supplementing VLM |
| **Model support** | Gemini only (in practice) | Model-agnostic via OpenRouter |
| **Agent architecture** | Single flat agent, no hierarchy | Hierarchical agents with task decomposition |
| **Turn structure** | Screenshot -> single LLM call -> single button | Screenshot -> multiple tool-call steps -> action sequence + explanation |

### Key Takeaways for Our Project

1. **Image enhancement is worth borrowing.** Their 3x upscale + contrast/saturation boost is a simple, effective preprocessing step. We should consider this for our vision pipeline, especially in separate-VLM mode where the small model might benefit from cleaner input.

2. **Request-response emulator protocol is sound.** Their approach of having the controller explicitly request screenshots (rather than polling or timer-based) is clean. Our emulator interface should follow a similar synchronous request-response pattern.

3. **Their project validates the "one button at a time" failure mode.** Even with memory reading giving the LLM exact coordinates, progress is painfully slow at one button per API call. This confirms our design decision to allow button sequences per turn.

4. **Their lack of task hierarchy shows why we need one.** Without any goal system, the agent drifts. The notepad accumulates vague objectives but nothing drives the agent to complete them systematically. Our recursive task decomposition with clear completion conditions addresses this directly.

5. **Their memory reading is a crutch we should not adopt.** The fact that they needed to inject X/Y coordinates and map names suggests the VLM alone struggled with spatial awareness. Rather than reading RAM, our approach should solve this through better prompting, OCR, and allowing the agent to build its own spatial understanding over time.

6. **Notepad auto-summarization is a reasonable pattern.** We should consider a similar mechanism for our state files -- when they grow too large, trigger a summarization pass. Though our _hide system partially addresses this by reducing what is loaded per turn.

7. **The name-entry keyboard layout in the prompt is a useful workaround.** For Pokemon specifically, this kind of game-knowledge-in-the-prompt is exactly where our architecture would put it (in the system prompt / agent-managed state), not in the harness code.

---

## Summary

martoast/LLM-Pokemon-Red is a straightforward, early-stage implementation that connects mGBA to Gemini via a socket bridge. It is simple, functional, and demonstrates the basic loop of screenshot-to-LLM-to-button-press. However, it is limited by its one-button-per-call design, reliance on RAM reading for game state, lack of any goal or task system, absence of OCR, and tight coupling to both Pokemon Red and Google Gemini. Our architecture addresses nearly all of these limitations by design, particularly through the task hierarchy, screen-only philosophy, action sequences, configurable vision pipeline, and game-agnostic harness.
