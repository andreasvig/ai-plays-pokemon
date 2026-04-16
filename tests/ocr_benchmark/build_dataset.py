"""Build the OCR benchmark dataset from existing run screenshots.

Copies a curated selection of screenshots and generates clean (no-grid) versions.
Run once to populate images/, then manually verify ground_truth.json.

Usage:
    python tests/ocr_benchmark/build_dataset.py
"""

import json
import shutil
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).parent.parent.parent
IMAGES_DIR = Path(__file__).parent / "images"
IMAGES_DIR.mkdir(exist_ok=True)

# Curated screenshots: (source_path, output_name, category, expected_text)
# expected_text is the dialogue/UI text visible on screen (empty for no-text screens)
SOURCES = [
    # --- Dialogue: Oak ---
    (
        "local/runs/2026-04-11_21-16-29_phase5_test/screenshots/00002_turn_2.png",
        "dialogue_oak_unsafe",
        "dialogue",
        "OAK: It's unsafe!\nWild POKéMON live in tall grass!",
    ),
    (
        "local/runs/2026-04-11_14-15-58_phase5_test/screenshots/00006_turn_6.png",
        "dialogue_oak_oldage",
        "dialogue",
        "But now, in my old age, I have\nonly these three left.",
    ),
    (
        "local/runs/2026-04-10_20-34-20_phase5_test/screenshots/00016_turn_16.png",
        "dialogue_oak_pokeballs",
        "dialogue",
        "Inside those three POKé BALLS are\nPOKéMON.",
    ),
    (
        "local/runs/2026-04-10_20-34-20_phase5_test/screenshots/00015_turn_15.png",
        "dialogue_oak_choose",
        "dialogue",
        "Which one will you choose for\nyourself?",
    ),
    (
        "local/runs/2026-04-11_21-16-29_phase5_test/screenshots/00004_turn_4.png",
        "dialogue_oak_haha",
        "dialogue",
        "Haha!",
    ),
    # --- Dialogue: Green ---
    (
        "local/runs/2026-04-11_21-16-29_phase5_test/screenshots/00003_turn_3.png",
        "dialogue_green_waiting",
        "dialogue",
        "GREEN: Gramps!\nI'm fed up with waiting!",
    ),
    (
        "local/runs/2026-04-11_21-16-29_phase5_test/screenshots/00005_turn_5.png",
        "dialogue_green_nofair",
        "dialogue",
        "GREEN: Hey! Gramps! No fair!\nWhat about me?",
    ),
    # --- Overworld: no text (controls) ---
    (
        "local/runs/2026-04-10_20-34-20_phase5_test/screenshots/00001_turn_1.png",
        "overworld_bedroom",
        "overworld",
        "",
    ),
    (
        "local/runs/2026-04-10_20-34-20_phase5_test/screenshots/00010_turn_10.png",
        "overworld_viridian",
        "overworld",
        "",
    ),
    (
        "local/runs/2026-04-10_20-34-20_phase5_test/screenshots/00012_turn_12.png",
        "overworld_mart_inside",
        "overworld",
        "",
    ),
    (
        "local/runs/2026-04-10_20-34-20_phase5_test/screenshots/00005_turn_5.png",
        "overworld_lab_edge",
        "overworld",
        "",
    ),
]


def remove_grid_overlay(img: Image.Image) -> Image.Image:
    """Best-effort grid removal: scale down to native 240x160 (averages out grid lines)."""
    # The grid was drawn on top of the 3x upscaled image. Downscaling back to native
    # resolution averages the thin grid lines into the surrounding pixels, effectively
    # removing them. Then re-upscale with nearest-neighbor for clean pixels.
    native = img.resize((240, 160), Image.LANCZOS)
    return native.resize((720, 480), Image.NEAREST)


def build():
    ground_truth = {}

    for src_rel, name, category, expected_text in SOURCES:
        src = ROOT / src_rel
        if not src.exists():
            print(f"  SKIP (not found): {src_rel}")
            continue

        # Copy original (with grid)
        grid_path = IMAGES_DIR / f"{name}_grid.png"
        shutil.copy2(src, grid_path)

        # Generate clean version (no grid)
        img = Image.open(src)
        clean = remove_grid_overlay(img)
        clean_path = IMAGES_DIR / f"{name}_clean.png"
        clean.save(clean_path)

        # Ground truth entry
        ground_truth[f"{name}_grid"] = {
            "source": src_rel,
            "category": category,
            "has_grid": True,
            "expected_text": expected_text,
        }
        ground_truth[f"{name}_clean"] = {
            "source": src_rel,
            "category": category,
            "has_grid": False,
            "expected_text": expected_text,
        }

        print(f"  OK: {name} ({category})")

    # Write ground truth
    gt_path = Path(__file__).parent / "ground_truth.json"
    with open(gt_path, "w") as f:
        json.dump(ground_truth, f, indent=2, ensure_ascii=False)

    print(f"\nDataset: {len(ground_truth)} images ({len(ground_truth)//2} originals x2 grid/clean)")
    print(f"Ground truth: {gt_path}")
    print(f"Images: {IMAGES_DIR}")
    print("\nTo add battle/menu screenshots:")
    print("  1. Play the game to a battle or menu screen")
    print("  2. Take a screenshot with: python -c \"from src.emulator.emulator import *; ...\"")
    print("  3. Add entry to SOURCES in this script and re-run")


if __name__ == "__main__":
    build()
