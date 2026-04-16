"""Informal benchmark: check if the OCR pipeline captures all key dialogue.

Compares the cleaned OCR output from a run against the known FireRed dialogue
sequence from the before_oak snapshot through starter selection. We don't care
which turn captured which line — just that every line appears *somewhere* in
the combined OCR output.

Usage:
    python tests/ocr_benchmark/test_dialogue_coverage.py [run_folder]
    # Default: picks the latest run
"""

import json
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

# ── Expected dialogue (FireRed, before_oak snapshot → starter selection) ────
# Player name: Player, Rival name: GREEN
# Source: Bulbapedia Professor Oak/Quotes + verified against successful runs.
# Each entry is one dialogue box. Spelling matches the game's own rendering.
EXPECTED_DIALOGUE = [
    "OAK: Hey! Wait! Don't go out!",
    "It's unsafe! Wild POKéMON live in tall grass!",
    "You need your own POKéMON for your protection. I know! Here, come with me!",
    "GREEN: Gramps! I'm fed up with waiting!",
    "GREEN? Let me think... Oh, that's right, I told you to come! Just wait!",
    "Here, Player! There are three POKéMON here!",
    "Haha! The POKéMON are held inside these POKé BALLS.",
    "When I was young, I was a serious POKéMON TRAINER.",
    "But now, in my old age, I have only these three left.",
    "You can have one. Go on, choose!",
    "GREEN: Hey! Gramps! No fair! What about me?",
    "OAK: Be patient, GREEN. You can have one, too!",
    "Now, Player. Inside those three POKé BALLS are POKéMON. Which one will you choose for yourself?",
    # Starter-dependent (Bulbasaur path)
    "So, Player, you want to go with the GRASS POKéMON BULBASAUR?",
    # Starter-dependent (Charmander path)
    # "So, Player, you're claiming the FIRE POKéMON CHARMANDER?",
    "This POKéMON is really quite energetic!",
    "Player received the",  # "BULBASAUR from PROF. OAK!" or "CHARMANDER..."
    "Do you want to give a nickname to this",
]

# Alternate phrasings the game uses depending on starter choice
ALTERNATE_LINES = {
    "So, Player, you want to go with the GRASS POKéMON BULBASAUR?": [
        "So, Player, you're claiming the FIRE POKéMON CHARMANDER?",
        "So, Player, you want the WATER POKéMON SQUIRTLE?",
        "So, Player, you want to go with the GRASS BULBASAUR?",
        "So, Player, you're claiming the FIRE CHARMANDER?",
    ],
}


def normalize(text: str) -> str:
    """Normalize for fuzzy comparison."""
    text = text.lower()
    text = text.replace("é", "e").replace("É", "e")
    text = text.replace("@", "e").replace("&", "e")
    text = re.sub(r'[^\w\s]', '', text)
    text = ' '.join(text.split())
    return text


def find_best_match(needle: str, haystack: str) -> float:
    """Find the best fuzzy match score of needle in haystack."""
    n = normalize(needle)
    h = normalize(haystack)
    # Check direct containment first
    if n in h:
        return 1.0
    # Sliding window similarity
    words_n = n.split()
    words_h = h.split()
    if not words_n:
        return 0.0
    best = 0.0
    window = len(words_n) + 3  # allow some slack
    for i in range(max(1, len(words_h) - window + 1)):
        chunk = ' '.join(words_h[i:i + window])
        sim = SequenceMatcher(None, n, chunk).ratio()
        best = max(best, sim)
    return best


def run_test(run_dir: Path):
    events_file = run_dir / "events.jsonl"
    if not events_file.exists():
        print(f"No events.jsonl in {run_dir}")
        return

    # Collect all cleaned OCR text into one big string
    with open(events_file) as f:
        events = [json.loads(l) for l in f if l.strip()]

    ocr_texts = []
    for e in events:
        if e.get("type") == "ocr_flush":
            cleaned = e.get("cleaned", "")
            if cleaned:
                ocr_texts.append(cleaned)

    combined = "\n".join(ocr_texts)

    if not combined.strip():
        print("WARNING: No OCR text found in this run!")
        print()

    # Score each expected line
    found = 0
    missed = 0
    partial = 0
    total = len(EXPECTED_DIALOGUE)

    print(f"Run: {run_dir.name}")
    print(f"OCR events: {len(ocr_texts)} flushes, {len(combined)} chars combined")
    print()
    print(f"{'#':>2} {'Score':>6} {'Status':<10} Expected line")
    print("-" * 90)

    for i, line in enumerate(EXPECTED_DIALOGUE, 1):
        # Check main line + alternates
        candidates = [line] + ALTERNATE_LINES.get(line, [])
        best_score = 0.0
        best_candidate = line
        for cand in candidates:
            score = find_best_match(cand, combined)
            if score > best_score:
                best_score = score
                best_candidate = cand

        if best_score >= 0.8:
            status = "FOUND"
            found += 1
        elif best_score >= 0.5:
            status = "PARTIAL"
            partial += 1
        else:
            status = "MISSED"
            missed += 1

        display_line = best_candidate if best_candidate != line else line
        print(f"{i:>2} {best_score:>5.0%}  {status:<10} {display_line[:75]}")

    print()
    print(f"Results: {found}/{total} found, {partial} partial, {missed} missed")
    coverage = (found + partial * 0.5) / total * 100
    print(f"Coverage: {coverage:.0f}%")

    return {"found": found, "partial": partial, "missed": missed, "total": total, "coverage": coverage}


def main():
    if len(sys.argv) > 1:
        run_dir = Path(sys.argv[1])
    else:
        runs_dir = Path("local/runs")
        dirs = sorted(runs_dir.iterdir(), reverse=True)
        if not dirs:
            print("No runs found.")
            return
        run_dir = dirs[0]

    run_test(run_dir)


if __name__ == "__main__":
    main()
