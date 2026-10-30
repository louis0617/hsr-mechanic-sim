"""S-class shared gaps: 1218102 + 11306102."""
from __future__ import annotations

from hsrsim.rules.shared_gaps import (
    SPARKLE_11306102_FREE_VAR,
    jiaoqiu_hearth_kindle_atk_flat,
    jiaoqiu_hearth_kindle_atk_pct,
)
from hsrsim.simulator.combat_rules import CombatRules
from hsrsim.simulator.types import ActionType


def test_hearth_kindle_steps_and_cap():
    assert jiaoqiu_hearth_kindle_atk_pct(0.80) == 0.0
    assert jiaoqiu_hearth_kindle_atk_pct(0.949) == 0.0
    assert abs(jiaoqiu_hearth_kindle_atk_pct(0.95) - 0.6) < 1e-9
    assert abs(jiaoqiu_hearth_kindle_atk_pct(1.40) - 2.4) < 1e-9
    assert abs(jiaoqiu_hearth_kindle_atk_pct(1.412) - 2.4) < 1e-9
    assert abs(jiaoqiu_hearth_kindle_atk_flat(1.412, 1000.0) - 2400.0) < 1e-6


def test_loadout_applies_1218102_and_rules_can_strip():
    from hsrsim.axis.community import GUIDE_SPEED_JQ, community_jq_axis
    from hsrsim.axis.eval import play_axis_l2

    axis = community_jq_axis()
    on = play_axis_l2(
        axis,
        zone=True,
        max_cycles=5,
        speed_override=GUIDE_SPEED_JQ,
        keep_engine=True,
        jiaoqiu_trace_1218102=True,
        sparkle_trace_11306102=False,
    )
    off = play_axis_l2(
        axis,
        zone=True,
        max_cycles=5,
        speed_override=GUIDE_SPEED_JQ,
        keep_engine=True,
        jiaoqiu_trace_1218102=False,
        sparkle_trace_11306102=False,
    )
    assert on["feasible"] and off["feasible"]
    jq_on = on["engine"].state.find_char("jiaoqiu")
    jq_off = off["engine"].state.find_char("jiaoqiu")
    assert jq_on is not None and jq_off is not None
    assert float(jq_on.char.hearth_kindle_atk_flat) > 0.0
    assert float(jq_on.char.build.stats.atk) > float(jq_off.char.build.stats.atk)


def test_sparkle_11306102_arms_after_3_sp_same_turn():
    from hsrsim.axis.community import GUIDE_SPEED_JQ
    from hsrsim.teams.loader import load_team_spec
    from hsrsim.simulator.bots import GreedyBot
    from hsrsim.simulator.engine import Engine

    spec = load_team_spec("acheron_direct", speed_override=GUIDE_SPEED_JQ)
    sc = spec.scenario.model_copy(deep=True)
    sc.max_cycles = 20
    for e in sc.enemies:
        e.immortal = True
    eng = Engine(
        sc,
        ally_bots={},
        enemy_bot=GreedyBot(),
        random_seed=1,
        combat_rules=CombatRules(
            sparkle_trace_11306102=True,
            jiaoqiu_trace_1218102=False,
        ),
    )
    sparkle = eng.state.find_char("sparkle")
    assert sparkle is not None
    # Simulate NORMAL turn owned by acheron spending 3 SP
    from hsrsim.simulator.state import TurnContext

    eng.state.current_turn = TurnContext(
        owner_id="acheron", kind="NORMAL", phase="action"
    )
    eng.state.find_char("acheron").variables["_ally_turn_sp_consumed"] = 0.0
    for _ in range(3):
        eng.bus.emit(
            "sp_consumed",
            {"consumer_id": "acheron", "amount": 1, "action_id": "acheron_skill"},
        )
    assert float(sparkle.variables.get(SPARKLE_11306102_FREE_VAR, 0.0)) >= 1.0
    skill = next(a for a in sparkle.char.build.actions if a.type == ActionType.SKILL)
    assert eng._effective_sp_cost(sparkle, skill) == 0
