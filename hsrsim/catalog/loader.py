"""Load character JSON files into typed Character models."""
from __future__ import annotations

import json
from pathlib import Path

from hsrsim.simulator.types import Character

REPO_ROOT = Path(__file__).resolve().parents[2]
CHARACTERS_DIR = REPO_ROOT / "data" / "hsr" / "characters"


def _apply_flat_trace_side_stats(char: Character, raw: dict) -> Character:
    """Fold EHR / Effect RES from _meta.flat_traces into Stats (D9)."""
    meta = raw.get("_meta") or {}
    traces = meta.get("flat_traces") or {}
    side = meta.get("trace_side_stats_not_in_Stats_model") or {}
    ehr = traces.get("ehr", side.get("ehr"))
    effect_res = traces.get("effect_res", side.get("effect_res"))
    if ehr is None and effect_res is None:
        return char
    char = char.model_copy(deep=True)
    if ehr is not None:
        char.build.stats.ehr = float(ehr)
    if effect_res is not None:
        char.build.stats.effect_res = float(effect_res)
    return char


def _hoist_skill_ids(raw: dict) -> None:
    """Copy ``skill_level.skill_id`` onto each action for  SPBase lookup."""
    build = raw.get("build")
    if not isinstance(build, dict):
        return
    actions = build.get("actions")
    if not isinstance(actions, list):
        return
    for action in actions:
        if not isinstance(action, dict):
            continue
        if action.get("skill_id") is not None:
            continue
        level = action.get("skill_level") or {}
        if isinstance(level, dict) and level.get("skill_id") is not None:
            action["skill_id"] = int(level["skill_id"])


def _stamp_talent_sp_bonus(char: Character, raw: dict) -> Character:
    """Stamp talent skill id/level; SP max bonus only if registered in sp_team_max.json."""
    meta = raw.get("_meta") or {}
    levels = meta.get("skill_levels") or {}
    talent = levels.get("talent") or {}
    skill_id = talent.get("skill_id")
    level = talent.get("final") or talent.get("param_list_level")
    if skill_id is None or level is None:
        return char
    from hsrsim.rules.sp_caps import talent_sp_max_bonus_from_datamine

    char = char.model_copy(deep=True)
    char.talent_skill_id = int(skill_id)
    char.talent_skill_level = int(level)
    # Uses stamped ids; returns 0 unless skill_id is in sp_team_max.json.
    char.sp_team_max_bonus = talent_sp_max_bonus_from_datamine(char)
    return char


def _hoist_sp_pool_temp_max(raw: dict) -> None:
    """Copy schema_gaps ult_sp_overflow_cap onto ultimate actions when present."""
    meta = raw.get("_meta") or {}
    gaps = meta.get("schema_gaps") or []
    overflow = None
    for g in gaps:
        if isinstance(g, dict) and g.get("field") == "ult_sp_overflow_cap":
            overflow = int(float(g["value"]))
            break
    if overflow is None:
        return
    build = raw.get("build")
    if not isinstance(build, dict):
        return
    for action in build.get("actions") or []:
        if not isinstance(action, dict):
            continue
        if action.get("type") == "ultimate" and action.get("sp_pool_temp_max") is None:
            action["sp_pool_temp_max"] = overflow


def load_character(path: str | Path) -> Character:
    path = Path(path)
    with path.open(encoding="utf-8") as f:
        raw = json.load(f)
    _hoist_skill_ids(raw)
    _hoist_sp_pool_temp_max(raw)
    char = Character.model_validate(raw)
    char = _apply_flat_trace_side_stats(char, raw)
    return _stamp_talent_sp_bonus(char, raw)


def load_character_by_id(character_id: str) -> Character:
    candidates = [
        CHARACTERS_DIR / f"{character_id}.json",
        CHARACTERS_DIR / "supports" / f"{character_id}.json",
    ]
    for path in candidates:
        if path.is_file():
            return load_character(path)
    raise FileNotFoundError(f"Character not found: {character_id}")
