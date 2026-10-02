""" /  — shared counter gains + team SP caps."""
from __future__ import annotations

import pytest

from hsrsim.analytic.adapter import build_l1_input
from hsrsim.enemies.benchmark_dummy import build_benchmark_dummy
from hsrsim.catalog.loader import load_character_by_id
from hsrsim.rules.counter_gains import (
    l1_l2_rule_fingerprint,
    specs_for_resource,
    specs_to_l1_gain_rules,
)
from hsrsim.rules.sp_caps import (
    param_list_for_skill,
    talent_sp_max_bonus_from_datamine,
    team_sp_max_from_allies,
)
from hsrsim.simulator.engine import Engine
from hsrsim.simulator.triggers import EventBus
from hsrsim.simulator.types import ActionType, Character, Scenario


def _dummy_enemy() -> Character:
    return build_benchmark_dummy(ally_elements=["lightning"])


def test_acheron_direct_l1_l2_counter_rules_match():
    """L1 adapter gain rules ≡ L2 EventBus registered specs (source/increment/cap)."""
    specs = specs_for_resource("nihility_stacks")
    l2_fp = l1_l2_rule_fingerprint(specs)

    allies = [
        load_character_by_id(cid)
        for cid in ("acheron", "jiaoqiu", "sparkle", "aventurine")
    ]
    # Match paper-team eidolon/LC so turn_start E2 + mirage R1 fires compile.
    allies = [
        a.model_copy(
            update={
                "eidolon": 2 if a.id == "acheron" else 0,
                "light_cone_id": 23024 if a.id == "acheron" else a.light_cone_id,
            }
        )
        for a in allies
    ]
    enemy = _dummy_enemy()
    engine = Engine(
        Scenario(name="shared-rules", allies=allies, enemies=[enemy], max_rounds=1),
        random_seed=0,
    )
    assert engine.bus.registered_counter_gain_fingerprint("nihility_stacks") == l2_fp

    inp = build_l1_input(
        "acheron_direct",
        energy_hit=0.0,
        hits_per_100_av=0.0,
    )
    assert len(inp.counters) == 1
    counter = inp.counters[0]
    assert counter.name == "nihility_stacks"

    # Structural set: id / increment / per_action_cap / kind via shared fingerprint
    # plus L1 fires compiled from the same specs.
    l1_from_specs = specs_to_l1_gain_rules(specs, allies)
    assert len(counter.rules) == len(l1_from_specs) == len(specs)
    by_pred = {r.predicate: r for r in counter.rules}
    for spec, compiled in zip(
        sorted(specs, key=lambda s: s.id),
        sorted(l1_from_specs, key=lambda r: r.predicate),
        strict=True,
    ):
        rule = by_pred[spec.id]
        assert rule.increment == pytest.approx(spec.increment)
        assert rule.per_action_cap == pytest.approx(spec.per_action_cap)
        assert rule.increment == pytest.approx(compiled.increment)
        assert rule.per_action_cap == pytest.approx(compiled.per_action_cap)
        assert rule.fires == compiled.fires

    assert {s["id"] for s in l2_fp} == {r.predicate for r in counter.rules}


def test_T32_real_acheron_skill_dream_coef_is_2():
    """: paper Acheron — skill-keyed dream = R3+R2 (+ E2 turn_start on skill turns)."""
    inp = build_l1_input(
        "acheron_direct",
        energy_hit=0.0,
        hits_per_100_av=0.0,
    )
    counter = next(c for c in inp.counters if c.holder_id == "acheron")
    skill_gain = 0.0
    skill_gain_no_e2 = 0.0
    for rule in counter.rules:
        fires = (rule.fires or {}).get("acheron") or {}
        if fires.get("skill", 0.0) > 0:
            add = float(rule.increment) * float(fires["skill"])
            skill_gain += add
            if rule.predicate != "acheron_e2_turn_start":
                skill_gain_no_e2 += add
    assert skill_gain_no_e2 == pytest.approx(2.0)  # R3 + R2(knot)
    assert skill_gain == pytest.approx(3.0)  # + E2 on the same skill turn


def test_sparkle_talent_paramlist_and_sp_team_max():
    """1130604 L10 ParamList → #3[i]=2; acheron_direct sp_team_max=7."""
    params = param_list_for_skill(1130604, 10)
    assert params == [2.0, 0.04, 2.0, 3.0]
    # TextMapEN: "increases the max number of Skill Points by #3[i]" → index 2
    assert params[2] == pytest.approx(2.0)

    sparkle = load_character_by_id("sparkle")
    assert sparkle.talent_skill_id == 1130604
    assert talent_sp_max_bonus_from_datamine(sparkle) == 2

    allies = [
        load_character_by_id(cid)
        for cid in ("acheron", "jiaoqiu", "sparkle", "aventurine")
    ]
    assert team_sp_max_from_allies(allies) == 7

    enemy = _dummy_enemy()
    engine = Engine(
        Scenario(name="d1-spmax", allies=allies, enemies=[enemy], max_rounds=1),
        random_seed=0,
    )
    assert engine.state.sp_team_max == 7


def test_ult_overflow_to_10_and_basic_truncates_at_max():
    sparkle = load_character_by_id("sparkle")
    ult = next(a for a in sparkle.build.actions if a.type == ActionType.ULTIMATE)
    basic = next(a for a in sparkle.build.actions if a.type == ActionType.BASIC_ATTACK)
    assert ult.sp_pool_temp_max == 10
    assert ult.sp_cost == -6
    assert basic.sp_cost == -1

    enemy = _dummy_enemy()
    enemy.build.stats.speed = 1.0
    sparkle.build.stats.speed = 200.0
    engine = Engine(
        Scenario(name="d1-overflow", allies=[sparkle], enemies=[enemy], max_rounds=5),
        random_seed=0,
    )
    assert engine.state.sp_team_max == 7  # base 5 + talent 2

    # Fill to soft max, then ult may overflow to temp 10.
    engine.state.sp_team_pool = 7
    actor = engine.state.find_char("sparkle")
    assert actor is not None
    actor.energy_current = sparkle.build.stats.energy_max
    engine.execute_action(actor, ult, enemy.id)
    assert engine.state.sp_team_pool == 10

    # Basic at hard soft-cap of 7 would truncate; at 10 with soft_cap 7:
    # basic uses sp_team_max=7 as gain_cap → truncates back toward 7.
    engine.state.sp_team_pool = 7
    before = engine.state.sp_team_pool
    engine.execute_action(actor, basic, enemy.id)
    assert engine.state.sp_team_pool == 7  # already at max; gain wasted
    trunc = [e for e in engine.events if e.event_type == "sp_gain_truncated"]
    assert any(e.payload.get("waste", 0) >= 1 for e in trunc)

    # Explicit: from 6, basic applies +1 to 7 with no waste.
    engine.state.sp_team_pool = 6
    n_before = len([e for e in engine.events if e.event_type == "sp_gain_truncated"])
    engine.execute_action(actor, basic, enemy.id)
    assert engine.state.sp_team_pool == 7
    n_after = len([e for e in engine.events if e.event_type == "sp_gain_truncated"])
    assert n_after == n_before
    _ = before
