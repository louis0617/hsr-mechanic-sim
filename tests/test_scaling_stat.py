"""Scaling stat wiring and support stub sanity checks."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from hsrsim.graph.loader import load_character_by_id
from hsrsim.simulator.damage_zones import BaseZone, DamageContext
from hsrsim.simulator.types import ActionType, DamageType, Element

REPO = Path(__file__).resolve().parents[1]


def test_def_scaling_uses_defense_not_atk():
    ctx_atk = DamageContext(
        attacker_level=80,
        attacker_atk=3000.0,
        attacker_crit_rate=0.0,
        attacker_crit_dmg=0.5,
        attacker_break_effect=0.0,
        attacker_dmg_boost_pct=0.0,
        attacker_res_pen_pct=0.0,
        attacker_def_ignore_pct=0.0,
        attacker_def_reduction_pct=0.0,
        defender_level=80,
        defender_def=800.0,
        defender_res_pct=0.0,
        defender_vuln_pct=0.0,
        defender_mit_layers=[],
        defender_toughness_broken=False,
        skill_multiplier=1.0,
        damage_type=DamageType.DIRECT,
        element=Element.IMAGINARY,
    )
    ctx_def = DamageContext(**{**ctx_atk.__dict__, "attacker_atk": 2200.0})
    assert BaseZone().compute(ctx_atk) == pytest.approx(3000.0)
    assert BaseZone().compute(ctx_def) == pytest.approx(2200.0)


def test_aventurine_skill_has_no_damage_instances():
    char = load_character_by_id("aventurine")
    skill = next(a for a in char.build.actions if a.id == "aventurine_skill")
    assert skill.damage_instances == []


def test_aventurine_basic_scales_on_defense():
    char = load_character_by_id("aventurine")
    basic = next(a for a in char.build.actions if a.id == "aventurine_basic")
    assert len(basic.damage_instances) == 1
    assert basic.damage_instances[0].scaling_stat == "defense"


def test_memory_support_basics_use_hp_scaling():
    for cid in ("hyacine", "evernight", "cyrene"):
        char = load_character_by_id(cid)
        basic = next(a for a in char.build.actions if a.type == ActionType.BASIC_ATTACK)
        assert basic.damage_instances[0].scaling_stat == "hp", cid


def test_castorice_random_excludes_aventurine():
    spec = json.loads((REPO / "data/hsr/teams/castorice_random.json").read_text(encoding="utf-8"))
    assert "aventurine" not in spec["members"]
    assert set(spec["members"]) == {"castorice", "jiaoqiu", "sparkle", "ruan_mei"}
