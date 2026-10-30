"""D1.9: L1 and L2 must share the same scenario enemy snapshot."""
from __future__ import annotations

from hsrsim.analytic.adapter import build_l1_input
from hsrsim.enemies.benchmark_dummy import (
    DEFAULT_ELEMENT_RES,
    DUMMY_DEFENSE,
    DUMMY_LEVEL,
    DUMMY_SPEED,
    DUMMY_TOUGHNESS,
    ally_element_keys,
    enemy_snapshot,
)
from hsrsim.teams.loader import load_team_spec


def test_l1_l2_enemy_params_identical_acheron_direct():
    """Structural equality: defense, element_res, weaknesses, speed, toughness."""
    spec = load_team_spec("acheron_direct")
    l2_enemy = spec.scenario.enemies[0]
    inp = build_l1_input("acheron_direct", energy_hit=0.0, hits_per_100_av=0.0)
    assert inp.enemy is not None
    assert enemy_snapshot(inp.enemy) == enemy_snapshot(l2_enemy)

    ally_els = set(ally_element_keys(spec.scenario.allies))
    weak = set(enemy_snapshot(l2_enemy)["weaknesses"])
    assert weak == ally_els
    assert l2_enemy.level == DUMMY_LEVEL
    assert float(l2_enemy.build.stats.defense) == DUMMY_DEFENSE
    assert float(l2_enemy.build.stats.speed) == DUMMY_SPEED
    assert float(l2_enemy.toughness_max) == DUMMY_TOUGHNESS
    for el, res in l2_enemy.build.stats.element_res.items():
        if el in weak:
            assert res == 0.0
        else:
            assert res == DEFAULT_ELEMENT_RES
