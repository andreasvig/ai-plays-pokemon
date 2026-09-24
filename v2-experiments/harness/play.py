#!/usr/bin/env python3
"""Let a model play a few turns through the v2 (SkyEmu) backend.

    venv/bin/python v2-experiments/harness/play.py \
        --rom "v2-experiments/roms/Pokemon - Platinum Version (USA).nds" \
        --turns 10

This is a probe, not the benchmark. It deliberately reimplements the turn loop
in ~200 lines instead of wiring SkyEmu into src/agent/turn.py, because the
question it answers — can a model see and act on an NDS frame through this
backend at all — does not need OCR, the referee, TaskMaster, compaction or
provider profiles, and answering it inside the real harness would mean changing
the real harness before knowing the answer. Everything here stays under
v2-experiments/.

What it does NOT reproduce, so nothing read off a run here is comparable with a
board run: no OCR text channel, no memory/referee reads, no compaction (the
whole conversation is kept, older screenshots dropped), no savestate ladder, no
gate detection.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from frame import geometry, prepare  # noqa: E402
from record import FPS, Recorder     # noqa: E402
from skyemu import BUTTONS, SkyEmu   # noqa: E402

REPO = Path(__file__).resolve().parents[2]

# The buttons a DS has that a GBA does not. Offering X/Y on a GBA run would be
# offering a control the console has no way to deliver.
NDS_EXTRA = {"x", "y"}

ACTION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["reasoning", "actions"],
    "properties": {
        "reasoning": {
            "type": "string",
            "description": (
                "First: what changed on screen versus what you predicted last turn. "
                "Then: what you are doing this turn and why. End with a concrete "
                "prediction of what the next screenshot will show."
            ),
        },
        "actions": {
            "type": "array",
            "description": "1-5 actions, executed in order.",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["type", "button", "x", "y", "frames"],
                "properties": {
                    "type": {"type": "string", "enum": ["press", "tap", "wait"]},
                    "button": {"type": ["string", "null"],
                               "description": "for type=press; null otherwise"},
                    "x": {"type": ["number", "null"],
                          "description": "for type=tap: 0..1 across the touch screen, left to right"},
                    "y": {"type": ["number", "null"],
                          "description": "for type=tap: 0..1 down the touch screen, top to bottom"},
                    "frames": {"type": ["integer", "null"],
                               "description": "for type=wait: frames to let the game run (60 = 1 second)"},
                },
            },
        },
    },
}

SYSTEM = """You are playing {game} on an emulator, one turn at a time.

{screen_help}

Each turn you get a screenshot of the console as it is RIGHT NOW, and you reply
with the actions to take. The emulator is frozen while you think — the game
advances only when your actions run — so the screenshot is exactly the state
your actions will act on. There is no time pressure and no dropped input.

Your actions:
{action_help}

Rules:
- 1 to 5 actions per turn. Fewer is usually better: you cannot see what happens
  between them, so a long blind sequence is a guess.
- Use `wait` for animations, scrolling text and transitions.
- Nothing is read for you. Every fact you use must be visible in the screenshot.

Goal: {goal}"""

GBA_SCREEN_HELP = "The screenshot is the console's single screen."

NDS_SCREEN_HELP = """This is a Nintendo DS. The screenshot shows BOTH screens, stacked vertically
and separated by a thin grey line:

  - ABOVE the line: the TOP screen. Display only — you cannot touch it.
  - BELOW the line: the BOTTOM screen. This is the TOUCH SCREEN.

A `tap` uses coordinates on the BOTTOM screen only: x=0 is its left edge, x=1
its right edge, y=0 its top edge (just under the grey line), y=1 its bottom
edge. Do not use the whole image's height — y=0 is the line, not the top of the
picture."""


def action_help(system: str) -> str:
    buttons = sorted(BUTTONS) if system == "NDS" else sorted(set(BUTTONS) - NDS_EXTRA)
    lines = [f'- press a button: {{"type": "press", "button": "<{"|".join(buttons)}>"}}',
             '- let the game run: {"type": "wait", "frames": 120}']
    if system == "NDS":
        lines.insert(1, '- touch the bottom screen: {"type": "tap", "x": 0.5, "y": 0.4}')
    return "\n".join(lines)


def load_key() -> str:
    from dotenv import load_dotenv
    load_dotenv(REPO / ".env")
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        sys.exit("no OPENROUTER_API_KEY (looked in the environment and .env)")
    return key


def data_url(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode()


def execute(emu: SkyEmu, actions: list[dict], system: str) -> list[str]:
    """Run the model's actions, returning what was actually done.

    An action the console cannot deliver is dropped with a note rather than
    raising: the run is a probe of whether the loop works, and a model naming a
    button that does not exist is a finding about the prompt, not a crash.
    """
    done = []
    for act in actions[:5]:
        kind = (act.get("type") or "").lower()
        if kind == "press":
            name = (act.get("button") or "").lower()
            if name not in BUTTONS or (system != "NDS" and name in NDS_EXTRA):
                done.append(f"REJECTED press:{name or '?'} (not a button on this console)")
                continue
            emu.press(name)
            done.append(f"press:{name}")
        elif kind == "tap":
            if system != "NDS":
                done.append("REJECTED tap (no touch screen on this console)")
                continue
            x, y = act.get("x"), act.get("y")
            if x is None or y is None:
                done.append("REJECTED tap (no coordinates)")
                continue
            emu.tap(float(x), float(y))
            done.append(f"tap:{float(x):.2f},{float(y):.2f}")
        elif kind == "wait":
            frames = int(act.get("frames") or 120)
            frames = max(1, min(frames, 1800))
            emu.step(frames)
            done.append(f"wait:{frames}")
        else:
            done.append(f"REJECTED {kind or 'empty'} (unknown action)")
    return done


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rom", required=True, type=Path)
    ap.add_argument("--model", default="openai/gpt-6-astra")
    ap.add_argument("--effort", default="low", help="OpenRouter reasoning effort")
    ap.add_argument("--turns", type=int, default=10)
    ap.add_argument("--port", type=int, default=8099)
    ap.add_argument("--boot-frames", type=int, default=0,
                    help="frames to step before turn 1, to skip boot logos")
    ap.add_argument("--load", type=Path, help="savestate to load before turn 1")
    ap.add_argument("--save-state", type=Path, help="write a savestate after the boot step")
    ap.add_argument("--goal", default="Play toward beating the game.")
    ap.add_argument("--keep-images", type=int, default=3,
                    help="how many recent screenshots stay in the conversation")
    ap.add_argument("--no-divider", action="store_true",
                    help="do not draw the seam between the two DS screens")
    ap.add_argument("--video", choices=["off", "game", "realtime"], default="off",
                    help="record an MP4: 'game' is game time only (the machine is "
                         "frozen while the model thinks, so its thinking is simply "
                         "not in the stream); 'realtime' additionally holds the "
                         "frozen frame for as long as each call took.")
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parents[1] / "plays")
    args = ap.parse_args()

    from openai import OpenAI
    client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=load_key())

    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out = args.out / f"{stamp}_{args.rom.stem[:28].replace(' ', '-')}_{args.model.split('/')[-1]}"
    out.mkdir(parents=True, exist_ok=True)
    events = (out / "turns.jsonl").open("w")
    print(f"→ {out}")

    with SkyEmu(args.rom, port=args.port, log=out / "skyemu.log") as emu:
        if args.load:
            emu.load_state(args.load)
        if args.boot_frames:
            # Chunked so a long boot shows progress rather than one silent block.
            left = args.boot_frames
            while left > 0:
                emu.step(min(600, left))
                left -= 600
            print(f"booted {args.boot_frames} frames")
        if args.save_state:
            emu.save_state(args.save_state)

        # The recorder is attached AFTER the boot step, so the file starts at
        # turn 1 rather than with several thousand frames of logo. It samples
        # native frames — the upscale happens once, in ffmpeg.
        recorder = None
        if args.video != "off":
            _, w, h = geometry(emu.screen())
            recorder = Recorder(out / "run.mp4", size=(w, h))
            if recorder.start():
                emu.sampler = recorder.add
                print(f"recording {args.video} → {recorder.out_path.name} ({w}x{h} @ {FPS}fps)")
            else:
                print(f"no recording: {recorder.error}")
                recorder = None

        system = None
        messages: list[dict] = []
        history: list[tuple[int, str]] = []   # (turn, what ran) — kept as text forever
        totals = {"prompt": 0, "completion": 0, "cost": 0.0, "seconds": 0.0}

        for turn in range(1, args.turns + 1):
            png, meta = prepare(emu.screen(), divider=not args.no_divider)
            (out / f"turn-{turn:03d}.png").write_bytes(png)

            if system is None:
                system = meta["system"]
                messages.append({"role": "system", "content": SYSTEM.format(
                    game=args.rom.stem,
                    screen_help=NDS_SCREEN_HELP if system == "NDS" else GBA_SCREEN_HELP,
                    action_help=action_help(system),
                    goal=args.goal,
                )})
                print(f"system: {system}  frame: {meta['size'][0]}x{meta['size'][1]}  "
                      f"grid_overlay: {meta['grid_overlay']}")

            recap = ("\n".join(f"turn {t}: {a}" for t, a in history[-8:])
                     or "This is your first turn.")
            messages.append({"role": "user", "content": [
                {"type": "text", "text": f"Turn {turn}. What ran recently:\n{recap}\n\nThe screen now:"},
                {"type": "image_url", "image_url": {"url": data_url(png)}},
            ]})
            # Drop the pixels of older turns, keep their text. A screenshot is
            # ~1k tokens on every future turn otherwise, and the model has
            # already said in its own reasoning what it saw there.
            _prune_images(messages, args.keep_images)

            t0 = time.time()
            resp = client.chat.completions.create(
                model=args.model,
                messages=messages,
                response_format={"type": "json_schema", "json_schema": {
                    "name": "game_action", "strict": True, "schema": ACTION_SCHEMA}},
                extra_body={"reasoning": {"effort": args.effort},
                            "usage": {"include": True}},
            )
            elapsed = time.time() - t0
            if recorder and args.video == "realtime":
                # Nothing happened on the machine during those seconds, so the
                # faithful rendering of them is the frozen frame repeated. This
                # is the one part of the file that is constructed rather than
                # captured — hence its own mode.
                recorder.hold(elapsed)
            raw = resp.choices[0].message.content or "{}"
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = {"reasoning": raw, "actions": []}

            ran = execute(emu, parsed.get("actions") or [], system)
            history.append((turn, ", ".join(ran) or "nothing"))
            messages.append({"role": "assistant", "content": raw})

            usage = getattr(resp, "usage", None)
            rec = {
                "turn": turn, "seconds": round(elapsed, 2),
                "frame": meta, "reasoning": parsed.get("reasoning", ""),
                "requested": parsed.get("actions") or [], "ran": ran,
                "prompt_tokens": getattr(usage, "prompt_tokens", None),
                "completion_tokens": getattr(usage, "completion_tokens", None),
                "cost_usd": getattr(usage, "cost", None),
            }
            events.write(json.dumps(rec) + "\n")
            events.flush()
            totals["prompt"] += rec["prompt_tokens"] or 0
            totals["completion"] += rec["completion_tokens"] or 0
            totals["cost"] += rec["cost_usd"] or 0.0
            totals["seconds"] += elapsed
            print(f"[{turn:2d}] {elapsed:5.1f}s  {', '.join(ran) or '—':40.40s} "
                  f"{(parsed.get('reasoning') or '')[:90]}")

        png, meta = prepare(emu.screen(), divider=not args.no_divider)
        (out / f"turn-{args.turns + 1:03d}-final.png").write_bytes(png)
        emu.sampler = None

    video = None
    if recorder:
        video = recorder.stop()
        print(f"video: {video.name} — {recorder.frames} frames, {recorder.seconds:.1f}s"
              if video else f"video failed: {recorder.error}")

    events.close()
    (out / "summary.json").write_text(json.dumps({
        "model": args.model, "effort": args.effort, "rom": str(args.rom),
        "turns": args.turns, "system": system, "totals": totals,
        "video": {"mode": args.video, "file": video.name if video else None,
                  "frames": recorder.frames if recorder else 0,
                  "seconds": round(recorder.seconds, 1) if recorder else 0,
                  "error": recorder.error if recorder else None} if recorder else None,
    }, indent=2))
    print(f"\n{args.turns} turns · {totals['prompt']:,} prompt + {totals['completion']:,} "
          f"completion tokens · ${totals['cost']:.4f} · {totals['seconds']:.0f}s")
    print(f"→ {out}")
    return 0


def _prune_images(messages: list[dict], keep: int) -> None:
    """Keep the pixels of the `keep` most recent user turns; text the rest."""
    seen = 0
    for msg in reversed(messages):
        if msg["role"] != "user" or not isinstance(msg["content"], list):
            continue
        has_image = any(p.get("type") == "image_url" for p in msg["content"])
        if not has_image:
            continue
        seen += 1
        if seen > keep:
            msg["content"] = [p for p in msg["content"] if p.get("type") != "image_url"] + [
                {"type": "text", "text": "[screenshot dropped from context]"}]


if __name__ == "__main__":
    raise SystemExit(main())
