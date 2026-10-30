"""D1.10: realistic toughness recover + always_unbroken mode."""
from __future__ import annotations

from hsrsim.enemies.benchmark_dummy import build_benchmark_dummy
from hsrsim.simulator.bots import GreedyBot
from hsrsim.simulator.damage_zones import BrokenZone, DamageContext
from hsrsim.simulator.engine import Engine
from hsrsim.simulator.types import (
    ActionType,
    Character,
    DamageType,
    Element,
    Scenario,
)


def _striker() -> Character:
    return Character.model_validate(
        {
            "id": "striker",
            "name": "striker",
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
                    "crit_rate": 1.0,
                    "crit_dmg": 0.5,
                    "energy_max": 100.0,
                },
                "actions": [
                    {
                        "id": "striker_skill",
                        "name": "skill",
                        "type": "skill",
                        "damage_instances": [
                            {
                                "multiplier": 1.0,
                                "element": "lightning",
                                "damage_type": "direct",
                                "target": "single",
                                "toughness_dmg": 100.0,
                            }
                        ],
                        "energy_cost": 0.0,
                        "sp_cost": 0,
                        "applies_effects": [],
                        "effect_target": "self",
                        "variable_changes": {},
                        "requires": [],
                    },
                    {
                        "id": "striker_basic",
                        "name": "basic",
                        "type": "basic_attack",
                        "damage_instances": [
                            {
                                "multiplier": 1.0,
                                "element": "lightning",
                                "damage_type": "direct",
                                "target": "single",
                                "toughness_dmg": 100.0,
                            }
                        ],
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


def _enemy(*, toughness: float = 100.0, speed: float = 50.0) -> Character:
    enemy = build_benchmark_dummy(ally_elements=["lightning"], immortal=True)
    enemy.toughness_max = toughness
    enemy.build.stats.speed = speed
    return enemy


def test_break_then_enemy_turn_recovers_full_toughness():
    sc = Scenario(
        name="tough-recover",
        allies=[_striker()],
        enemies=[_enemy(toughness=100.0, speed=50.0)],
        max_cycles=5,
        toughness_mode="realistic",
    )
    eng = Engine(sc, ally_bot=GreedyBot(), enemy_bot=GreedyBot(), random_seed=1)
    ally = eng.state.find_char("striker")
    enemy = eng.state.enemies[0]
    assert ally is not None
    skill = next(a for a in ally.char.build.actions if a.type == ActionType.SKILL)
    eng.state.begin_normal_turn(ally)
    eng._execute_action(ally, skill, enemy.char.id, turn_kind="NORMAL")
    eng.state.end_normal_turn(ally)
    assert enemy.toughness_broken
    assert enemy.toughness_current == 0.0
    # Enemy's next normal turn start restores toughness.
    eng.state.begin_normal_turn(enemy)
    assert not enemy.toughness_broken
    assert abs(enemy.toughness_current - enemy.char.toughness_max) < 1e-9
    eng.state.end_normal_turn(enemy)


def test_always_unbroken_f_broken_is_unbroken():
    sc = Scenario(
        name="tough-always",
        allies=[_striker()],
        enemies=[_enemy(toughness=50.0, speed=1.0)],
        max_cycles=3,
        toughness_mode="always_unbroken",
    )
    eng = Engine(sc, ally_bot=GreedyBot(), enemy_bot=GreedyBot(), random_seed=1)
    result = eng.run()
    assert result.total_damage > 0
    for ev in eng.events:
        if ev.event_type != "damage":
            continue
        z = ev.payload.get("zone_breakdown") or {}
        assert abs(float(z.get("f_broken", 0.0)) - 0.9) < 1e-9
    assert eng.state.enemies[0].toughness_current == eng.state.enemies[0].char.toughness_max


def test_broken_zone_uptime_blend():
    zone = BrokenZone()
    ctx = DamageContext(
        attacker_level=80,
        attacker_atk=1000.0,
        attacker_crit_rate=0.0,
        attacker_crit_dmg=0.5,
        attacker_break_effect=0.0,
        attacker_dmg_boost_pct=0.0,
        attacker_res_pen_pct=0.0,
        attacker_def_ignore_pct=0.0,
        attacker_def_reduction_pct=0.0,
        defender_level=95,
        defender_def=1000.0,
        defender_res_pct=0.0,
        defender_vuln_pct=0.0,
        defender_mit_layers=[],
        defender_toughness_broken=False,
        defender_broken_uptime=0.4,
        skill_multiplier=1.0,
        damage_type=DamageType.DIRECT,
        element=Element.LIGHTNING,
    )
    assert abs(zone.compute(ctx) - 0.94) < 1e-9
