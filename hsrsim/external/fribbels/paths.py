"""Locate the vendored Fribbels hsr-optimizer checkout."""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

# User checkout: hsr-optimizer-main/ at repo root (preferred)
FRIBBELS_ROOT = REPO_ROOT / "hsr-optimizer-main"
if not FRIBBELS_ROOT.is_dir():
    FRIBBELS_ROOT = REPO_ROOT / "data" / "external" / "fribbels-ref"

CONDITIONALS_DIR = FRIBBELS_ROOT / "src" / "lib" / "conditionals" / "character"
DAMAGE_CALCULATOR_TS = (
    FRIBBELS_ROOT / "src" / "lib" / "optimization" / "engine" / "damage" / "damageCalculator.ts"
)

# Fribbels CharacterConfig.id → our JSON character id
FRIBBELS_ID_TO_CHAR: dict[str, str] = {
    "1407": "castorice",
    "1409": "hyacine",
    "1413": "evernight",
    "1415": "cyrene",
}

CHAR_TO_FRIBBELS_FILE: dict[str, str] = {
    "castorice": "1400/Castorice.ts",
    "hyacine": "1400/Hyacine.ts",
    "evernight": "1400/Evernight.ts",
    "cyrene": "1400/Cyrene.ts",
}


def character_ts_path(char_id: str) -> Path:
    rel = CHAR_TO_FRIBBELS_FILE.get(char_id)
    if rel is None:
        raise KeyError(f"No Fribbels mapping for character {char_id!r}")
    path = CONDITIONALS_DIR / rel
    if not path.is_file():
        raise FileNotFoundError(
            f"Fribbels character file missing: {path}\n"
            f"Expected hsr-optimizer at {FRIBBELS_ROOT}"
        )
    return path
