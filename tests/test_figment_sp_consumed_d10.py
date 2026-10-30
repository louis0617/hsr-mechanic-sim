"""D1.10 / E1.1d: sparkle_figment via sp_consumed on Sparkle + cipher aura vuln."""
from __future__ import annotations

from hsrsim.enemies.benchmark_dummy import build_benchmark_dummy
from hsrsim.graph.loader import load_character_by_id
from hsrsim.simulator.bots import GreedyBot
from hsrsim.simulator.effect_agg import figment_aura_vuln
from hsrsim.simulator.engine import Engine
from hsrsim.simulator.queries import effect_stacks
from hsrsim.simulator.types import (
    ActionType,
    Character,
    Effect,
    EffectModifier,
    EffectTarget,
    Scenario,
)


def _ally_spender() -> Character:
    return Character.model_validate(
        {
            "id": "spender",
            "name": "spender",
            "level": 80,
            "path": "nihility",
            "elements": ["lightning"],
            "weaknesses": [],
            "toughness_max": 100.0,
            "build": {
                "stats": {
                    "hp_max": 1_000_000.0,
                    "atk": 1000.0,
                    "defense": 0.0,
                    "speed": 200.0,
                    "crit_rate": 0.05,
                    "crit_dmg": 0.5,
                    "energy_max": 100.0,
                },
                "actions": [
                    {
                        "id": "spender_skill",
                        "name": "skill",
                        "type": "skill",
                        "damage_instances": [],
                        "energy_cost": 0.0,
                        "sp_cost": 1,
                        "applies_effects": [],
                        "effect_target": "self",
                        "variable_changes": {},
                        "requires": [],
                    },
                    {
                        "id": "spender_basic",
                        "name": "basic",
                        "type": "basic_attack",
                        "damage_instances": [],
                        "energy_cost": 0.0,
                        "sp_cost": -1,
                        "applies_effects": [],
                        "effect_target": "self",
                        "variable_changes": {},
                        "requires": [],
                    },
                ],
                "effects": [],
                "variables": [],
            },
        }
    )


def _scenario(*, with_sparkle: bool = True) -> Scenario:
    allies = [_ally_spender()]
    if with_sparkle:
        sparkle = load_character_by_id("sparkle").model_copy(deep=True)
        sparkle.build.stats.speed = 50.0  # act after spender
        allies.append(sparkle)
    enemy = build_benchmark_dummy(ally_elements=["lightning"], immortal=True)
    enemy.build.stats.speed = 1.0
    return Scenario(
        name="figment-d10",
        allies=allies,
        enemies=[enemy],
        max_cycles=2,
        toughness_mode="always_unbroken",
    )


def test_skill_spend_adds_one_figment_stack_on_sparkle():
    sc = _scenario()
    eng = Engine(sc, ally_bot=GreedyBot(), enemy_bot=GreedyBot(), random_seed=1)
    spender = eng.state.find_char("spender")
    sparkle = eng.state.find_char("sparkle")
    enemy = eng.state.enemies[0]
    assert spender is not None and sparkle is not None
    skill = next(a for a in spender.char.build.actions if a.type == ActionType.SKILL)
    before = effect_stacks(sparkle, "sparkle_figment")
    eng.state.begin_normal_turn(spender)
    eng._execute_action(spender, skill, enemy.char.id, turn_kind="NORMAL")
    eng.state.end_normal_turn(spender)
    assert effect_stacks(sparkle, "sparkle_figment") == before + 1
    assert effect_stacks(enemy, "sparkle_figment") == 0


def test_figment_stacks_cap_at_3_on_sparkle():
    sc = _scenario()
    eng = Engine(sc, ally_bot=GreedyBot(), enemy_bot=GreedyBot(), random_seed=1)
    spender = eng.state.find_char("spender")
    sparkle = eng.state.find_char("sparkle")
    enemy = eng.state.enemies[0]
    assert spender is not None and sparkle is not None
    skill = next(a for a in spender.char.build.actions if a.type == ActionType.SKILL)
    eng.state.sp_team_pool = 10
    for _ in range(5):
        eng.state.begin_normal_turn(spender)
        eng._execute_action(spender, skill, enemy.char.id, turn_kind="NORMAL")
        eng.state.end_normal_turn(spender)
    assert effect_stacks(sparkle, "sparkle_figment") == 3
    assert effect_stacks(enemy, "sparkle_figment") == 0


def test_cipher_on_attacker_boosts_figment_aura():
    figment = Effect(
        id="sparkle_figment",
        name="幻相",
        is_buff=True,
        target=EffectTarget.SELF,
        duration_turns=2,
        max_stacks=3,
        current_stacks=2,
        modifiers=[EffectModifier(target_stat="vuln", operation="add", value=0.04)],
    )
    cipher = Effect(
        id="sparkle_enemy_vuln",
        name="谜诡",
        is_buff=True,
        target=EffectTarget.ALL_ALLIES,
        duration_turns=3,
        max_stacks=1,
        current_stacks=1,
        modifiers=[EffectModifier(target_stat="vuln", operation="add", value=0.06)],
    )
    only_fig = figment_aura_vuln(
        attacker_effects=[],
        ally_effects_bags=[[figment]],
    )
    with_cipher = figment_aura_vuln(
        attacker_effects=[cipher],
        ally_effects_bags=[[figment]],
    )
    no_attacker_cipher = figment_aura_vuln(
        attacker_effects=[],
        ally_effects_bags=[[figment, cipher]],
    )
    assert abs(only_fig - 0.08) < 1e-9
    assert abs(with_cipher - 0.20) < 1e-9
    # Cipher on an ally bag but not on the attacker → no bonus.
    assert abs(no_attacker_cipher - 0.08) < 1e-9


def test_no_figment_without_sparkle():
    sc = _scenario(with_sparkle=False)
    eng = Engine(sc, ally_bot=GreedyBot(), enemy_bot=GreedyBot(), random_seed=1)
    spender = eng.state.find_char("spender")
    enemy = eng.state.enemies[0]
    assert spender is not None
    skill = next(a for a in spender.char.build.actions if a.type == ActionType.SKILL)
    eng.state.begin_normal_turn(spender)
    eng._execute_action(spender, skill, enemy.char.id, turn_kind="NORMAL")
    eng.state.end_normal_turn(spender)
    assert effect_stacks(enemy, "sparkle_figment") == 0


def test_figment_duration_ticks_on_sparkle_turns():
    """持续 2 回合按花火回合 turn_end 递减（非敌方回合）。"""
    sc = _scenario()
    eng = Engine(sc, ally_bot=GreedyBot(), enemy_bot=GreedyBot(), random_seed=1)
    spender = eng.state.find_char("spender")
    sparkle = eng.state.find_char("sparkle")
    enemy = eng.state.enemies[0]
    assert spender is not None and sparkle is not None
    skill = next(a for a in spender.char.build.actions if a.type == ActionType.SKILL)
    eng.state.begin_normal_turn(spender)
    eng._execute_action(spender, skill, enemy.char.id, turn_kind="NORMAL")
    eng.state.end_normal_turn(spender)
    eff = next(e for e in sparkle.active_effects if e.id == "sparkle_figment")
    assert eff.duration_turns == 2
    assert eff.tick_timing == "turn_end"
    # Enemy turn must not tick Sparkle-held figment.
    eng.state.begin_normal_turn(enemy)
    eng.state.end_normal_turn(enemy)
    eff = next(e for e in sparkle.active_effects if e.id == "sparkle_figment")
    assert eff.duration_turns == 2
    eng.state.begin_normal_turn(sparkle)
    eng.state.end_normal_turn(sparkle)
    eff = next(e for e in sparkle.active_effects if e.id == "sparkle_figment")
    assert eff.duration_turns == 1
    eng.state.begin_normal_turn(sparkle)
    eng.state.end_normal_turn(sparkle)
    assert effect_stacks(sparkle, "sparkle_figment") == 0
