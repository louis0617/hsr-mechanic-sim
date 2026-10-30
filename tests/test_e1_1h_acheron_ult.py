"""E1.1h: Rainblade erase, Thunder Core extras, talent RES, clear knots."""
from __future__ import annotations

import pytest

from hsrsim.analytic.adapter import build_l1_input
from hsrsim.enemies.benchmark_dummy import build_benchmark_dummy
from hsrsim.graph.loader import load_character_by_id
from hsrsim.simulator.bots import GreedyBot
from hsrsim.simulator.engine import Engine
from hsrsim.simulator.queries import effect_stacks
from hsrsim.simulator.triggers import (
    CRIMSON_KNOT_ID,
    SLASHED_DREAM_ID,
    crimson_knot_template,
)
from hsrsim.simulator.types import Scenario


def _engine() -> Engine:
    acheron = load_character_by_id("acheron").model_copy(deep=True)
    acheron.eidolon = 2
    acheron.build.stats.speed = 200.0
    enemy = build_benchmark_dummy(ally_elements=["lightning"], immortal=True)
    enemy.build.stats.speed = 1.0
    sc = Scenario(
        name="e11h",
        allies=[acheron],
        enemies=[enemy],
        max_cycles=1,
        toughness_mode="always_unbroken",
    )
    return Engine(sc, ally_bot=GreedyBot(), random_seed=0)


def test_rainblade_s9_adds_erase_and_thunder_hits():
    eng = _engine()
    enemy = eng.state.enemies[0]
    enemy.apply_effect(crimson_knot_template(stacks=9), source_id="acheron")
    actor = eng.state.find_char("acheron")
    assert actor is not None
    actor.variables[SLASHED_DREAM_ID] = 9.0
    ult = next(a for a in actor.char.build.actions if a.id == "acheron_ult")
    before = [
        ev for ev in eng.events if ev.event_type == "damage"
    ]
    eng.execute_inserted_action(actor, ult, enemy.char.id)
    hits = [
        ev
        for ev in eng.events
        if ev.event_type == "damage" and ev.payload.get("attacker") == "acheron"
    ]
    # 3 rainblade + 3 erase + 1 resurge + 6 thunder = 13
    assert len(hits) == 13
    assert effect_stacks(enemy, CRIMSON_KNOT_ID) == 0
    assert not any(e.id == "acheron_ult_res_down" for e in enemy.active_effects)
    thunder = next(
        (e for e in actor.active_effects if e.id == "acheron_thunder_core"), None
    )
    assert thunder is not None
    assert int(thunder.current_stacks) == 3


def test_s0_no_erase():
    eng = _engine()
    actor = eng.state.find_char("acheron")
    enemy = eng.state.enemies[0]
    assert actor is not None
    actor.variables[SLASHED_DREAM_ID] = 9.0
    ult = next(a for a in actor.char.build.actions if a.id == "acheron_ult")
    eng.execute_inserted_action(actor, ult, enemy.char.id)
    hits = [
        ev
        for ev in eng.events
        if ev.event_type == "damage" and ev.payload.get("attacker") == "acheron"
    ]
    # 3 rainblade + 1 resurge + 6 thunder (no knot → no thunder stacks, still 6 extras)
    # Thunder extras fire on resurge regardless of knot (行迹「发动黄泉返渡时」).
    assert len(hits) == 10


def test_rainblade_s5_erase_per_slash():
    """S=5 → erase 3 then 2: MV 0.60 then 0.45; thunder stacks=2."""
    eng = _engine()
    enemy = eng.state.enemies[0]
    enemy.apply_effect(crimson_knot_template(stacks=5), source_id="acheron")
    actor = eng.state.find_char("acheron")
    assert actor is not None
    actor.variables[SLASHED_DREAM_ID] = 9.0
    ult = next(a for a in actor.char.build.actions if a.id == "acheron_ult")
    eng.execute_inserted_action(actor, ult, enemy.char.id)
    hits = [
        ev
        for ev in eng.events
        if ev.event_type == "damage" and ev.payload.get("attacker") == "acheron"
    ]
    # 3 rainblade + 2 erase + 1 resurge + 6 thunder = 12
    assert len(hits) == 12
    thunder = next(
        (e for e in actor.active_effects if e.id == "acheron_thunder_core"), None
    )
    assert thunder is not None
    assert int(thunder.current_stacks) == 2


def test_l1_unarmored_all_attackers_and_ult_extras():
    inp = build_l1_input(
        "acheron_direct", energy_hit=0.0, hits_per_100_av=0.0
    )
    unarm = [b.id for b in inp.buffs if "lc_unarmored" in b.id]
    assert set(unarm) == {
        "lc_unarmored__acheron",
        "lc_unarmored__jiaoqiu",
        "lc_unarmored__sparkle",
        "lc_unarmored__aventurine",
    }
    assert len(inp.action_contexts["acheron"]["ult"]) >= 13
    ult0 = inp.action_contexts["acheron"]["ult"][0]
    basic0 = inp.action_contexts["acheron"]["basic"][0]
    assert ult0.attacker_res_pen_pct >= basic0.attacker_res_pen_pct + 0.19


def test_l1_can_disable_e11h():
    inp = build_l1_input(
        "acheron_direct",
        energy_hit=0.0,
        hits_per_100_av=0.0,
        acheron_ult_e11h=False,
    )
    assert len(inp.action_contexts["acheron"]["ult"]) == 4
