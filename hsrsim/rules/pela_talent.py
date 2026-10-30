"""Pela talent extra energy from AvatarSkillConfig ParamList (fail-loud)."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TABLE = REPO_ROOT / "data" / "hsr" / "tables" / "pela_talent_energy.json"


@lru_cache(maxsize=1)
def _table() -> dict:
    raw = json.loads(TABLE.read_text(encoding="utf-8"))
    by_lv = raw.get("energy_by_level")
    if not isinstance(by_lv, dict) or not by_lv:
        raise KeyError("pela_talent_energy.json missing energy_by_level")
    return {str(k): float(v) for k, v in by_lv.items()}


def talent_extra_energy(level: int | None) -> float:
    """L12 ParamList #1 = 11. Missing level or row is UNKNOWN — do not default."""
    if level is None:
        raise KeyError("Pela talent_skill_level is None; cannot look up extra energy")
    key = str(int(level))
    rows = _table()
    if key not in rows:
        raise KeyError(f"Pela talent extra energy UNKNOWN for L{level}")
    return float(rows[key])
