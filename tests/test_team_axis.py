"""M9: team-level axis / GSD tests."""
from __future__ import annotations

import pytest

from hsrsim.graph.loader import load_character_by_id
from hsrsim.graph.mechanism_isolation import MechanismPairing, evaluate_pairing
from hsrsim.simulator.bots import GreedyBot, HeuristicBot, build_team_axis_oracle_bots
from hsrsim.simulator.engine import Engine
from hsrsim.teams.loader import TEAM_IDS, load_team_spec
from hsrsim.teams.runner import evaluate_team_axis_gsd, evaluate_team_gsd


@pytest.mark.parametrize("team_id", TEAM_IDS)
def test_team_scenario_has_four_allies(team_id: str):
    spec = load_team_spec(team_id)
    assert len(spec.scenario.allies) == 4
    assert spec.main_dps in {a.id for a in spec.scenario.allies}


def test_effect_applies_to_enemy():
    spec = load_team_spec("acheron_direct")
    scenario = spec.scenario
    jiaoqiu = next(a for a in scenario.allies if a.id == "jiaoqiu")
    skill = next(a for a in jiaoqiu.build.actions if a.id == "jiaoqiu_skill")
    assert skill.effect_target == "enemy"

    engine = Engine(scenario, ally_bot=GreedyBot(), enemy_bot=GreedyBot(), random_seed=1)
    jq_state = engine.state.find_char("jiaoqiu")
    enemy = engine.state.enemies[0]
    engine.execute_action(jq_state, skill, enemy.char.id)
    assert any(e.id == "ashen_roast" for e in enemy.active_effects)


def test_team_battle_runs(team_id: str = "acheron_direct"):
    spec = load_team_spec(team_id)
    result = Engine(
        spec.scenario,
        ally_bots={a.id: HeuristicBot() for a in spec.scenario.allies},
        enemy_bot=GreedyBot(),
        random_seed=0,
    ).run()
    assert result.total_damage > 0
    assert len(result.actions_taken) > 0


@pytest.mark.parametrize("team_id", TEAM_IDS)
def test_team_gsd_non_negative(team_id: str):
    g = evaluate_team_gsd(team_id, n_sims=3, base_seed=0, oracle_depth=3)
    assert g >= 0.0


def test_mechanism_isolation_firefly_sparkle():
    """E1: 流萤超击破 × 花火暴伤 → zone 无交集（机制隔离）."""
    p = MechanismPairing(
        "firefly", "流萤", "v2", "firefly_enhanced_skill",
        "sparkle", "花火", "v2.3", "sparkle_skill",
    )
    r = evaluate_pairing(p, load_char=load_character_by_id)
    assert r.isolated
    assert "f_crit" in r.support_zones
    assert "f_crit" not in r.main_zones


def test_team_axis_oracle_bots_wired():
    spec = load_team_spec("acheron_direct")
    bots = build_team_axis_oracle_bots(
        spec.scenario, spec.main_dps, ("acheron", "sparkle"), max_depth=2, beam_width=2
    )
    assert bots["acheron"] is bots["sparkle"]
    assert bots["jiaoqiu"] is not bots["acheron"]


def test_team_axis_gsd_positive_acheron_direct():
    """MVP stop-step: layered Oracle + AV should beat all-Greedy on acheron_direct."""
    pytest.skip(
        "D7/D8: 终结技通道未接通，GSD 度量残缺轴，正确期望值无法确定。"
        "BenchmarkLoadout 已落地但仍保持 skip。"
        "恢复条件：C4–C6 完成后重新评估期望值。见 docs/V1.0/SKIPPED_TESTS.md"
    )
    g = evaluate_team_axis_gsd(
        "acheron_direct",
        n_sims=4,
        base_seed=1,
        oracle_depth=5,
        beam_width=3,
    )
    assert g > 0.1
