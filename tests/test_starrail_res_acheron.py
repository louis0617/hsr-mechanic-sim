"""Smoke scenario multipliers must match StarRailRes (index_min/cn)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
RES_ROOT = REPO_ROOT / "data" / "external" / "StarRailRes"
SMOKE = REPO_ROOT / "data" / "hsr" / "scenarios" / "acheron_smoke.json"

ULT_LV = 9  # 0-based index for skill level 10 in params[]


@pytest.fixture(scope="module")
def res_skills() -> dict:
    path = RES_ROOT / "character_skills.json"
    if not path.is_file():
        pytest.skip(f"StarRailRes not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def smoke_ult_instances() -> list[dict]:
    data = json.loads(SMOKE.read_text(encoding="utf-8"))
    ult = next(
        a for a in data["allies"][0]["build"]["actions"] if a["id"] == "acheron_ult"
    )
    return ult["damage_instances"]


def test_acheron_ult_multipliers_match_starrail_res(
    res_skills: dict, smoke_ult_instances: list[dict]
) -> None:
    rainblade_mv = res_skills["130814"]["params"][ULT_LV][0]
    stygian_mv = res_skills["130817"]["params"][ULT_LV][0]

    assert rainblade_mv == pytest.approx(0.24)
    assert stygian_mv == pytest.approx(1.2)

    assert smoke_ult_instances[0]["multiplier"] == pytest.approx(rainblade_mv)
    assert smoke_ult_instances[1]["multiplier"] == pytest.approx(rainblade_mv)
    assert smoke_ult_instances[2]["multiplier"] == pytest.approx(rainblade_mv)
    assert smoke_ult_instances[3]["multiplier"] == pytest.approx(stygian_mv)

    assert smoke_ult_instances[0]["target"] == "single"
    assert smoke_ult_instances[3]["target"] == "aoe"
