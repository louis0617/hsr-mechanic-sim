"""Shared combat resource helpers (SP caps from datamine, etc.)."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from hsrsim.simulator.types import Character

_REPO = Path(__file__).resolve().parents[2]
_SKILL_CONFIG = (
    _REPO
    / "data"
    / "external"
    / "turnbasedgamedata"
    / "ExcelOutput"
    / "AvatarSkillConfig.json"
)
_SP_MAX_RULES = _REPO / "data" / "hsr" / "triggers" / "sp_team_max.json"


@lru_cache(maxsize=1)
def _skill_config_rows() -> list[dict]:
    return json.loads(_SKILL_CONFIG.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _sp_max_rules() -> dict:
    return json.loads(_SP_MAX_RULES.read_text(encoding="utf-8"))


def param_list_for_skill(skill_id: int, level: int) -> list[float]:
    rows = [
        r
        for r in _skill_config_rows()
        if int(r.get("SkillID", -1)) == int(skill_id) and int(r.get("Level", -1)) == int(level)
    ]
    if not rows:
        raise ValueError(f"AvatarSkillConfig missing SkillID={skill_id} Level={level}")
    pl = rows[0].get("ParamList") or []
    return [float(p["Value"]) for p in pl]


def talent_bonus_rule(skill_id: int) -> dict | None:
    for row in _sp_max_rules().get("talent_bonuses") or []:
        if int(row["skill_id"]) == int(skill_id):
            return dict(row)
    return None


def talent_sp_max_bonus_from_datamine(char: Character) -> int:
    """Read SP max bonus from the character's talent ParamList when registered.

    Only talents listed in ``data/hsr/triggers/sp_team_max.json`` contribute.
    Index comes from TextMap placeholders (e.g. #3[i] → ParamList[2]).
    """
    talent_id = getattr(char, "talent_skill_id", None)
    talent_level = getattr(char, "talent_skill_level", None)
    if talent_id is None or talent_level is None:
        return 0
    rule = talent_bonus_rule(int(talent_id))
    if rule is None:
        return 0
    params = param_list_for_skill(int(talent_id), int(talent_level))
    idx = int(rule["param_index_1based"]) - 1
    if idx < 0 or idx >= len(params):
        raise ValueError(
            f"talent {talent_id} L{talent_level} ParamList too short for "
            f"#{rule['param_index_1based']}[i]: {params}"
        )
    return int(round(params[idx]))


def team_sp_max_from_allies(allies: list[Character], *, base: int | None = None) -> int:
    if base is None:
        base = int(_sp_max_rules().get("base", 5))
    return int(base + sum(talent_sp_max_bonus_from_datamine(c) for c in allies))
