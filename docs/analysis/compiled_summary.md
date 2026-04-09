# Compiled Analysis: What We Can Learn From Existing LLM-Plays-Pokemon Projects

## Projects Analyzed


| Project                               | Emulator          | LLM Framework                  | RAM Reading?      | Vision?           | Task System? |
| ------------------------------------- | ----------------- | ------------------------------ | ----------------- | ----------------- | ------------ |
| downthecrop/pokemon-llm               | mGBA (Lua socket) | OpenAI-compatible API          | Heavy             | Direct multimodal | None         |
| martoast/LLM-Pokemon-Red              | mGBA (Lua socket) | Google Gemini                  | Yes (coords, map) | Direct multimodal | None         |
| cicero225/llm_pokemon_scaffold        | PyBoy (threaded)  | Anthropic/Google/OpenAI direct | Heavy             | Direct multimodal | None         |
| CalebDeLeeuw/PokemonLLMAgentBenchmark | PyBoy (direct)    | smolagents (Anthropic/Ollama)  | Heavy             | Effectively none  | None         |


---

## Key Finding: Every Project Reads From RAM

All four projects read game memory for state data (player coordinates, HP, map ID, badges, inventory, etc.). None of them play with only human-level information. This is the biggest validation of our approach being genuinely novel - **no existing project we found attempts screen-only play.**

Some even claim to be screen-only while secretly reading RAM (martoast's README says "just like a human would" but reads coordinates and map IDs from memory).

---

## Emulation: Two Clear Options


| Approach                  | Used By                 | Pros                                                                              | Cons                                                                             |
| ------------------------- | ----------------------- | --------------------------------------------------------------------------------- | -------------------------------------------------------------------------------- |
| **mGBA + Lua TCP socket** | downthecrop, martoast   | Mature emulator, supports GBA too, clean socket protocol                          | Requires external emulator process, Lua scripting, file-based screenshot handoff |
| **PyBoy (Python native)** | cicero225, CalebDeLeeuw | Pure Python, direct API, in-memory screenshots, frame control, speed manipulation | Game Boy only (no GBA), Python 3.11 compatibility issues noted                   |


**PyBoy is the stronger choice for us.** It's simpler (no external process, no socket, no Lua), gives direct Python access to screenshots, and supports speed control for testing. The only downside is no GBA support, but for Game Boy Pokemon games it's ideal.

---

## Input Approaches


| Approach                           | Used By                       | Notes                                                                             |
| ---------------------------------- | ----------------------------- | --------------------------------------------------------------------------------- |
| One button per LLM call            | martoast                      | Extremely slow, high API cost                                                     |
| Button sequences per call          | downthecrop, cicero225        | Much more efficient, our approach matches this                                    |
| Touch/coordinate pathfinding       | downthecrop, cicero225        | LLM says "go to (5,3)", system pathfinds. Clever but relies on RAM collision data |
| Code generation (tool composition) | CalebDeLeeuw (via smolagents) | LLM writes Python to compose tool calls                                           |


**Our design of allowing action sequences (e.g., "RRRRAAA") per turn is validated.** The one-button-per-call approach is clearly too slow and expensive.

---

## Ideas Worth Borrowing

### 1. Grid Overlay on Screenshots

**From:** downthecrop, cicero225

Drawing a grid on screenshots gives the LLM a coordinate system for spatial reasoning. Both projects overlay a tile grid with labels. This works without RAM reading - it's just image preprocessing.

### 2. Image Enhancement / Upscaling

**From:** martoast, cicero225

Game Boy screenshots are tiny (160x144). Pre-processing with upscaling (3-4x), contrast boost, and saturation adjustment helps VLMs read text and identify objects. Low-cost, high-value preprocessing.

### 3. Dataset Collection Pipeline

**From:** CalebDeLeeuw

Recording (screenshot, reasoning, action, result) tuples and pushing to HuggingFace Hub. Useful for analyzing agent behavior, debugging, and potentially training future models. Directly feeds into our "Learning & Memory Agent" future idea.

### 4. Request-Response Emulator Protocol

**From:** martoast

Don't poll or use timers. The agent explicitly requests a screenshot when ready, processes it, sends actions, then requests the next one. Clean synchronization.

### 5. Three-Stage Summarization

**From:** cicero225

When compressing history: (1) extract facts, (2) clean/validate facts, (3) produce summary. More robust than a single-pass "summarize this" call.

### 6. Location Labeling

**From:** cicero225

Letting the agent name locations it discovers ("Pokecenter entrance", "ledge near Route 2") and persisting those labels is a good spatial memory pattern. Fits naturally into our agent-managed state system.

### 7. Exploration Tracking

**From:** cicero225

Marking areas as explored/unexplored/recently-visited prevents the agent from going in circles. Could be implemented as part of agent-managed world atlas state.

### 8. ASCII/Text Maps

**From:** downthecrop, cicero225

Alongside visual screenshots, providing a text-based map representation gives the LLM a non-visual way to reason about space. In our case, the agent could build these maps itself from VLM observations.

---

## What Every Project Gets Wrong (From Our Perspective)

1. **RAM reading as a crutch.** Every project falls back on memory reads for reliable state. None solve the hard problem of extracting game state from the screen alone.
2. **No task decomposition.** All four use flat single-agent loops. None have goal hierarchies, sub-agents, or recursive planning. For short demos this works, but for playing through a full game it won't scale.
3. **Minimal context management.** Most accumulate history until a threshold then summarize everything. None have our per-turn "I saw / I thought / I did" compression approach.
4. **Pokemon Red hardcoded.** All four are deeply coupled to Pokemon Red's memory layout. None are game-agnostic.
5. **No OCR.** Only CalebDeLeeuw uses pytesseract, and it's unreliable on Game Boy pixel fonts. None have a continuous OCR system.

---

## How Our Architecture Compares


| Feature            | Existing Projects                   | Our Design                                             |
| ------------------ | ----------------------------------- | ------------------------------------------------------ |
| Information source | RAM + screen                        | Screen only                                            |
| Game specificity   | Pokemon Red hardcoded               | Game-agnostic harness                                  |
| Agent hierarchy    | Single flat agent                   | Recursive task decomposition with sub-agents           |
| State management   | Rolling summary or flat notepad     | Agent-managed free-form files with `_hide` system      |
| Context per turn   | Accumulate then bulk-summarize      | Per-turn compression to "I saw / I thought / I did"    |
| Vision             | Direct multimodal (+ RAM as backup) | Configurable separate VLM or direct multimodal         |
| OCR                | None or unreliable pytesseract      | Continuous background OCR                              |
| Stuck detection    | None or prompt-based hints          | Max turns per task, forced failure, parent re-planning |
| Model support      | 1-3 hardcoded providers             | Model-agnostic via OpenRouter                          |


**Our design is significantly more ambitious.** No existing project attempts what we're building. The closest is downthecrop in terms of feature richness, but it still relies fundamentally on RAM reading and has no task system.

---

## Recommended Technical Choices Based on Analysis

- **Emulator:** PyBoy (Python native, direct API, used by 2/4 projects)
- **Screenshot preprocessing:** Upscale 3-4x + grid overlay + contrast/saturation boost
- **Emulator protocol:** Request-response (not timer-based)
- **Data logging:** Record turn traces for later analysis (inspired by CalebDeLeeuw's dataset pipeline)

