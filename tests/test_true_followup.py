"""Cyrene zone: True DMG as f_trueDmg = 1 + pct (Fribbels trueDmgMulti; 原文额外实例等价)."""
from __future__ import annotations

import pytest

from hsrsim.enemies.benchmark_dummy import build_benchmark_dummy
from hsrsim.graph.loader import load_character_by_id
from hsrsim.simulator.engine import Engine
from hsrsim.simulator.types import Scenario


def test_cyrene_true_dmg_zone_multiplies_final():
    castorice = load_character_by_id("castorice")
    cyrene = load_character_by_id("cyrene")
    enemy = build_benchmark_dummy(immortal=True, weaknesses=["quantum"])
    scenario = Scenario(
        name="true_dmg_zone",
        allies=[castorice, cyrene],
        enemies=[enemy],
        max_cycles=1,
    )
    engine = Engine(scenario, random_seed=42)
    cyrene_s = engine.state.find_char("cyrene")
    cyrene_s.char.build.stats.energy_max = 0.0
    castorice_s = engine.state.find_char("castorice")
    enemy_s = engine.state.enemies[0]

    skill = next(a for a in cyrene_s.char.build.actions if a.id == "cyrene_skill")
    engine._execute_action(cyrene_s, skill, None)

    aura = next(e for e in castorice_s.active_effects if e.id == "cyrene_true_aura")
    assert aura is not None

    basic = next(a for a in castorice_s.char.build.actions if a.id == "castorice_basic")
    dmg_inst = basic.damage_instances[0]
    hp_before = enemy_s.hp_current
    events_before = len(engine.events)

    engine._resolve_damage(castorice_s, enemy_s, dmg_inst, basic)

    damage_events = [
        e for e in engine.events[events_before:] if e.event_type == "damage"
    ]
    followup_events = [
        e for e in engine.events[events_before:] if e.event_type == "true_followup"
    ]
    assert followup_events == []
    primary = damage_events[0].payload["raw_damage"]
    zones = damage_events[0].payload["zone_breakdown"]
    assert zones["f_trueDmg"] == pytest.approx(1.24)
    assert primary > 0
    assert enemy_s.hp_current == pytest.approx(hp_before - primary)
