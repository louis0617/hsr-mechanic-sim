"""A1.5: StarRailRes #N[i|fK] placeholder resolution."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from hsrsim.external.starrail_res import resolve_skill_param

REPO_ROOT = Path(__file__).resolve().parents[1]
RES_SKILLS = (
    REPO_ROOT / "data" / "external" / "StarRailRes" / "index_new" / "en" / "character_skills.json"
)


@pytest.fixture(scope="module")
def sparkle_skills() -> dict:
    if not RES_SKILLS.is_file():
        pytest.skip(f"StarRailRes skills not found: {RES_SKILLS}")
    all_skills = json.loads(RES_SKILLS.read_text(encoding="utf-8"))
    return {
        "ult": all_skills["130603"],
        "talent": all_skills["130604"],
    }


def test_sparkle_ult_skill_points_recovered(sparkle_skills: dict) -> None:
    """大招 desc: Recovers #2[i] Skill Points → params[..., 4, ...] → 4."""
    ult = sparkle_skills["ult"]
    # Any level: index 2 is constantly 4 in Res params
    resolved = resolve_skill_param(ult["desc"], ult["params"], level=1)
    assert "#2[i]" in resolved
    assert resolved["#2[i]"]["raw"] == pytest.approx(4.0)
    assert resolved["#2[i]"]["value"] == 4
    assert resolved["#2[i]"]["as_percent"] is False


def test_sparkle_talent_skill_point_cap_bonus(sparkle_skills: dict) -> None:
    """天赋 desc: max Skill Points by #3[i] → params [2, 0.075, 2, 3] → 2 (not 3)."""
    talent = sparkle_skills["talent"]
    # Use max level row (same #3 value at all levels)
    level = len(talent["params"])
    resolved = resolve_skill_param(talent["desc"], talent["params"], level=level)
    assert "#3[i]" in resolved
    assert resolved["#3[i]"]["raw"] == pytest.approx(2.0)
    assert resolved["#3[i]"]["value"] == 2
    assert resolved["#3[i]"]["index"] == 3
    # #4[i] is max stacks (=3); must not be confused with #3
    assert resolved["#4[i]"]["value"] == 3
