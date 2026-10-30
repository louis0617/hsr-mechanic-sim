"""E1.1d: R1 counts enemy debuffs; figment on Sparkle never feeds dream."""
from __future__ import annotations

import pytest

from hsrsim.enemies.benchmark_dummy import build_benchmark_dummy
from hsrsim.graph.loader import load_character_by_id
from hsrsim.rules.counter_gains import (
    figment_counts_for_dream,
    zone_proc_counts_for_dream,
)
from hsrsim.simulator.engine import Engine
from hsrsim.simulator.queries import effect_stacks
from hsrsim.simulator.triggers import (
    APPLY_SOURCE_ACTION_CAST,
    SLASHED_DREAM_ID,
    SPARKLE_FIGMENT_ID,
)
from hsrsim.simulator.types import ActionType, Scenario


def _team_scenario(*ids: str) -> Scenario:
    allies = [load_character_by_id(cid).model_copy(deep=True) for cid in ids]
    for a in allies:
        a.build.stats.speed = 200.0
    elems: list[str] = []
    for a in allies:
        for e in a.elements:
            elems.append(e.value if hasattr(e, "value") else str(e))
    enemy = build_benchmark_dummy(ally_elements=elems, immortal=True)
    enemy.build.stats.speed = 1.0
    return Scenario(
        name="r1",
        allies=allies,
        enemies=[enemy],
        max_cycles=2,
        toughness_mode="always_unbroken",
    )


def _run_skill(engine: Engine, actor_id: str) -> None:
    actor = engine.state.find_char(actor_id)
    assert actor is not None
    skill = next(a for a in actor.char.build.actions if a.type == ActionType.SKILL)
    enemy = engine.state.enemies[0]
    engine.state.sp_team_pool = max(engine.state.sp_team_pool, max(skill.sp_cost, 0) + 1)
    engine.state.begin_normal_turn(actor)
    engine._execute_action(actor, skill, enemy.char.id, turn_kind="NORMAL")
    engine.state.end_normal_turn(actor)


def test_figment_flag_archived_false_zone_default_true():
    assert figment_counts_for_dream() is False
    assert zone_proc_counts_for_dream() is True


def test_sparkle_skill_figment_does_not_grant_dream():
    """花火战技仅队友增益 + 幻相（挂自身）→ 残梦 +0（E1.1d）。"""
    sc = _team_scenario("acheron", "sparkle")
    eng = Engine(sc, random_seed=0)
    ach = eng.state.find_char("acheron")
    sparkle = eng.state.find_char("sparkle")
    assert ach is not None and sparkle is not None
    before = float(ach.variables.get(SLASHED_DREAM_ID, 0.0))
    _run_skill(eng, "sparkle")
    after = float(ach.variables.get(SLASHED_DREAM_ID, 0.0))
    assert effect_stacks(sparkle, SPARKLE_FIGMENT_ID) >= 1
    assert effect_stacks(eng.state.enemies[0], SPARKLE_FIGMENT_ID) == 0
    assert after == pytest.approx(before)


def test_T33_figment_on_sparkle_never_enemy_applied():
    """D1.13 排除结论保留：幻相不计残梦；理由改为从未施加于敌。"""
    sc = _team_scenario("acheron", "sparkle")
    eng = Engine(sc, random_seed=0)
    ach = eng.state.find_char("acheron")
    sparkle = eng.state.find_char("sparkle")
    assert ach is not None and sparkle is not None
    before = float(ach.variables.get(SLASHED_DREAM_ID, 0.0))
    _run_skill(eng, "sparkle")
    after = float(ach.variables.get(SLASHED_DREAM_ID, 0.0))
    assert effect_stacks(sparkle, SPARKLE_FIGMENT_ID) >= 1
    assert after == pytest.approx(before)


def test_T34_jiaoqiu_skill_plus_figment_dream_plus_one():
    """椒丘战技敌方减益 + 幻相（不计）→ 残梦 +1。"""
    sc = _team_scenario("acheron", "jiaoqiu", "sparkle")
    eng = Engine(sc, random_seed=0)
    ach = eng.state.find_char("acheron")
    sparkle = eng.state.find_char("sparkle")
    assert ach is not None and sparkle is not None
    before = float(ach.variables.get(SLASHED_DREAM_ID, 0.0))
    _run_skill(eng, "jiaoqiu")
    after = float(ach.variables.get(SLASHED_DREAM_ID, 0.0))
    assert effect_stacks(sparkle, SPARKLE_FIGMENT_ID) >= 1
    assert after - before == pytest.approx(1.0)


def test_T35_acheron_skill_plus_figment_dream_plus_one():
    """黄泉战技（仅 R3；无 stub 减益）+ 幻相（不计）→ 残梦 +1。"""
    sc = _team_scenario("acheron", "sparkle")
    eng = Engine(sc, random_seed=0)
    ach = eng.state.find_char("acheron")
    sparkle = eng.state.find_char("sparkle")
    assert ach is not None and sparkle is not None
    before = float(ach.variables.get(SLASHED_DREAM_ID, 0.0))
    _run_skill(eng, "acheron")
    after = float(ach.variables.get(SLASHED_DREAM_ID, 0.0))
    assert effect_stacks(sparkle, SPARKLE_FIGMENT_ID) >= 1
    assert after - before == pytest.approx(1.0)


def test_followup_applies_effects_are_action_cast():
    """FOLLOWUP_COUNTS_AS_ACTION：追加攻击 applies_effects → action_cast，可触发 R2。"""
    from hsrsim.simulator.combat_rules import CombatRules
    from hsrsim.simulator.types import Effect, EffectModifier, EffectTarget

    acheron = load_character_by_id("acheron").model_copy(deep=True)
    fu = next(a for a in acheron.build.actions if a.type == ActionType.SKILL).model_copy(
        deep=True
    )
    fu.id = "acheron_fu"
    fu.type = ActionType.FOLLOW_UP
    fu.sp_cost = 0
    fu.applies_effects = ["test_debuff"]
    acheron.build.actions.append(fu)
    acheron.build.effects.append(
        Effect(
            id="test_debuff",
            name="test",
            is_buff=False,
            target=EffectTarget.SINGLE_ENEMY,
            duration_turns=2,
            max_stacks=1,
            current_stacks=1,
            modifiers=[
                EffectModifier(target_stat="vuln", operation="add", value=0.1)
            ],
        )
    )

    enemy = build_benchmark_dummy(ally_elements=["lightning"], immortal=True)
    sc = Scenario(name="fu-cast", allies=[acheron], enemies=[enemy], max_cycles=1)
    eng = Engine(
        sc,
        random_seed=0,
        combat_rules=CombatRules(followup_counts_as_action=True),
    )
    seen: list[str] = []
    orig = eng.bus.emit

    def hooked(event, payload=None):
        payload = dict(payload or {})
        if event == "effect_applied" and payload.get("effect_id") == "test_debuff":
            seen.append(str(payload.get("apply_source")))
        return orig(event, payload)

    eng.bus.emit = hooked  # type: ignore[method-assign]
    actor = eng.state.find_char("acheron")
    assert actor is not None
    dream_before = float(actor.variables.get(SLASHED_DREAM_ID, 0.0))
    eng.state.begin_normal_turn(actor)
    eng._execute_action(actor, fu, enemy.id, turn_kind="NORMAL")
    eng.state.end_normal_turn(actor)
    assert seen == [APPLY_SOURCE_ACTION_CAST]
    assert float(actor.variables.get(SLASHED_DREAM_ID, 0.0)) - dream_before == pytest.approx(
        1.0
    )
