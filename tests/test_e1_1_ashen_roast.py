"""E1.1: Ashen Roast effect, talent/skill apply, dream cap with figment."""
from __future__ import annotations

import pytest

from hsrsim.enemies.benchmark_dummy import build_benchmark_dummy
from hsrsim.graph.loader import load_character_by_id
from hsrsim.rules.counter_gains import figment_counts_for_dream, zone_proc_counts_for_dream
from hsrsim.simulator.effect_agg import aggregate_effects
from hsrsim.simulator.engine import Engine
from hsrsim.simulator.queries import effect_stacks
from hsrsim.simulator.triggers import SLASHED_DREAM_ID
from hsrsim.simulator.types import ActionType, Scenario


def _scenario(*ids: str) -> Scenario:
    allies = [load_character_by_id(cid).model_copy(deep=True) for cid in ids]
    for a in allies:
        a.build.stats.speed = 200.0
    elems: list[str] = []
    for a in allies:
        for e in a.elements:
            elems.append(e.value if hasattr(e, "value") else str(e))
    enemy = build_benchmark_dummy(ally_elements=elems, immortal=True)
    enemy.build.stats.speed = 1.0
    enemy.build.stats.effect_res = 0.0
    return Scenario(
        name="e11",
        allies=allies,
        enemies=[enemy],
        max_cycles=3,
        toughness_mode="always_unbroken",
    )


def _run(engine: Engine, actor_id: str, action_type: ActionType) -> None:
    actor = engine.state.find_char(actor_id)
    assert actor is not None
    action = next(a for a in actor.char.build.actions if a.type == action_type)
    enemy = engine.state.enemies[0]
    if action.sp_cost > 0:
        engine.state.sp_team_pool = max(engine.state.sp_team_pool, action.sp_cost + 1)
    engine.state.begin_normal_turn(actor)
    engine._execute_action(actor, action, enemy.char.id, turn_kind="NORMAL")
    engine.state.end_normal_turn(actor)


def test_zone_default_true_figment_archived_false():
    assert zone_proc_counts_for_dream() is True
    assert figment_counts_for_dream() is False


def test_ashen_full_stack_vuln_35_percent():
    jq = load_character_by_id("jiaoqiu")
    tmpl = next(e for e in jq.build.effects if e.id == "ashen_roast")
    eff = tmpl.model_copy(deep=True)
    eff.current_stacks = 5
    totals = aggregate_effects([eff], side="defender")
    assert totals["vuln"] == pytest.approx(0.35)


def test_skill_applies_two_stacks_and_dream_plus_one_with_figment():
    """战技双次烬煨 + 幻相 → 残梦 +1（共享 cap），层数 2。"""
    sc = _scenario("acheron", "jiaoqiu", "sparkle")
    eng = Engine(sc, random_seed=0)
    ach = eng.state.find_char("acheron")
    assert ach is not None
    before = float(ach.variables.get(SLASHED_DREAM_ID, 0.0))
    _run(eng, "jiaoqiu", ActionType.SKILL)
    after = float(ach.variables.get(SLASHED_DREAM_ID, 0.0))
    enemy = eng.state.enemies[0]
    assert effect_stacks(enemy, "ashen_roast") == 2
    assert after - before == pytest.approx(1.0)


def test_no_legacy_jiaoqiu_vuln_effects():
    jq = load_character_by_id("jiaoqiu")
    ids = {e.id for e in jq.build.effects}
    assert "ashen_roast" in ids
    assert "jiaoqiu_vuln" not in ids
    assert "jiaoqiu_dot_boost" not in ids


def test_ashen_dot_ticks_on_enemy_turn_start():
    sc = _scenario("jiaoqiu")
    eng = Engine(sc, random_seed=0)
    _run(eng, "jiaoqiu", ActionType.SKILL)
    enemy = eng.state.enemies[0]
    assert effect_stacks(enemy, "ashen_roast") >= 1
    hp_before = enemy.hp_current
    eng.state.begin_normal_turn(enemy)
    assert enemy.hp_current < hp_before


def test_ashen_duration_two_turns_refresh_on_reapply():
    """持续 2 回合；再施加刷新；与敌方 turn_start 递减一致。"""
    sc = _scenario("jiaoqiu")
    eng = Engine(sc, random_seed=0)
    _run(eng, "jiaoqiu", ActionType.BASIC_ATTACK)
    enemy = eng.state.enemies[0]
    eff = next(e for e in enemy.active_effects if e.id == "ashen_roast")
    assert eff.duration_turns == 2
    assert eff.tick_timing == "turn_start"
    eng.state.begin_normal_turn(enemy)
    eng.state.end_normal_turn(enemy)
    eff = next(e for e in enemy.active_effects if e.id == "ashen_roast")
    assert eff.duration_turns == 1
    _run(eng, "jiaoqiu", ActionType.BASIC_ATTACK)
    eff = next(e for e in enemy.active_effects if e.id == "ashen_roast")
    assert eff.duration_turns == 2
