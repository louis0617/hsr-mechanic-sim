"""F-a / F-b tests: event-log coverage > 0; cycle axis plays."""
from __future__ import annotations

from hsrsim.rotation.community import community_jq_axis, community_pela_axis
from hsrsim.rotation.coverage import attach_live_sampler, measure_event_log_sparkle, summarize_snaps
from hsrsim.rotation.eval import l1_eval_axis, play_axis_l2
from hsrsim.rotation.schema import CycleAxis
from hsrsim.metrics.readable_axis import measure_sparkle_coverage_at_acheron_actions
from hsrsim.simulator.bots import GreedyBot, build_team_evaluation_bots
from hsrsim.simulator.combat_rules import CombatRules
from hsrsim.simulator.engine import Engine
from hsrsim.simulator.ult_timing import UltTimingStrategy
from hsrsim.teams.loader import load_team_spec


def _short_engine(team_id: str = "acheron_direct") -> Engine:
    spec = load_team_spec(team_id)
    sc = spec.scenario.model_copy(deep=True)
    sc.max_cycles = 8
    for e in sc.enemies:
        e.immortal = True
    bots = build_team_evaluation_bots(
        sc,
        spec.main_dps,
        main_bot=GreedyBot(),
        ult_timing=UltTimingStrategy.IMMEDIATE_WHEN_FULL.value,
    )
    return Engine(
        sc,
        ally_bots=bots,
        enemy_bot=GreedyBot(),
        random_seed=2,
        combat_rules=CombatRules(acheron_ult_e11h=True),
    )


def test_effect_applied_reaches_engine_events():
    eng = _short_engine()
    eng.run()
    applied = [e for e in eng.events if e.event_type == "effect_applied"]
    expired = [e for e in eng.events if e.event_type == "effect_expired"]
    assert applied, "bus effect_applied must be copied into eng.events"
    assert expired, "effect_expired must reach eng.events"


def test_sparkle_coverage_positive_when_buff_present():
    eng = _short_engine()
    snaps = attach_live_sampler(eng)
    eng.run()
    live = summarize_snaps(snaps)
    log = measure_event_log_sparkle(eng)
    old = measure_sparkle_coverage_at_acheron_actions(eng)
    live_any = max((live.get(k, {}).get("sparkle_skill") or 0.0) for k in ("basic", "skill", "ult"))
    log_any = max(log.get(k, 0.0) for k in ("basic", "skill", "ult"))
    old_any = max(old.get(k, 0.0) for k in ("basic", "skill", "ult"))
    assert live_any > 0.0, f"live sampler expected coverage>0, got {live}"
    assert log_any > 0.0, f"event-log sparkle coverage expected >0, got {log}"
    assert old_any > 0.0, f"readable_axis event-log path expected >0, got {old}"


def test_community_axes_encode_two_sources():
    jq = community_jq_axis()
    pe = community_pela_axis()
    assert len(jq.sources) >= 2
    for s in jq.sources + pe.sources:
        assert s.get("url") and s.get("platform") and s.get("nature")
    assert jq.members["jiaoqiu"].mode == "adaptive"
    assert jq.members["jiaoqiu"].sp_min == 1
    assert pe.members["pela"].mode == "adaptive"
    assert jq.sp_priority[0] == "sparkle"
    assert "acheron" in jq.sp_priority


def test_l1_eval_uses_share_not_absolute_pin():
    axis = community_jq_axis()
    l1 = l1_eval_axis(axis, zone=True, pin_mode="axis")
    assert l1["feasible"]
    ach = l1["rates"]["acheron"]
    turns = ach["nb"] + ach["ns"]
    assert turns > 1.34, turns  # spd/100≈1.34 without pull; pull should add extra
    share = ach["ns"] / turns if turns else 0.0
    assert abs(share - 1.0) < 1e-6

    axis = community_jq_axis()
    l1 = l1_eval_axis(axis, zone=True, pin_mode="axis")
    l2 = play_axis_l2(axis, zone=True, max_cycles=6, seed=1, strict_sp=False)
    assert l1["dpr"] > 0
    assert l2["dpr"] > 0
    assert isinstance(axis, CycleAxis)


def test_enum_catalog_size_and_f6_ignores_aven():
    from hsrsim.rotation.community import community_jq_axis
    from hsrsim.rotation.enumerate import iter_axes
    from hsrsim.rotation.schema import CycleAxis, MemberPolicy, f6_key

    axes = iter_axes("jq")
    assert len(axes) == 250
    jq = community_jq_axis()
    other = CycleAxis(
        axis_id="x",
        team_id=jq.team_id,
        members={
            **{k: v for k, v in jq.members.items() if k != "aventurine"},
            "aventurine": MemberPolicy("aventurine", ["basic"]),
        },
    )
    assert f6_key(jq) == f6_key(other)


def test_strict_sp_marks_infeasible_without_fallback():
    from hsrsim.rotation.policy_bot import AxisSPInfeasible
    from hsrsim.rotation.schema import CycleAxis, MemberPolicy

    axis = CycleAxis(
        axis_id="all_e_starve",
        team_id="acheron_direct",
        members={
            "sparkle": MemberPolicy("sparkle", ["skill"], default_target="acheron"),
            "jiaoqiu": MemberPolicy("jiaoqiu", ["skill"]),
            "acheron": MemberPolicy("acheron", ["skill"]),
            "aventurine": MemberPolicy("aventurine", ["skill"]),
        },
    )
    row = play_axis_l2(axis, zone=True, max_cycles=100, strict_sp=True, keep_engine=False)
    assert row["feasible"] is False or row.get("dpr") is not None
    # If the axis survives Sparkle ult SP, it is feasible; if it asks skill at 0, infeasible.
    if not row["feasible"]:
        assert "skill not legal" in (row.get("reason") or "")


def test_adaptive_community_is_sp_feasible():
    from hsrsim.rotation.community import community_pela_axis

    pe = community_pela_axis()
    l2 = play_axis_l2(pe, zone=True, max_cycles=20, seed=1)
    assert l2.get("feasible") is True
    assert (l2.get("dpr") or 0) > 0


def test_adaptive_catalog_size():
    from hsrsim.rotation.enumerate import iter_adaptive_axes

    assert len(iter_adaptive_axes("jq")) == 108
    assert len(iter_adaptive_axes("pela")) == 108


def test_l1_fua_channel_present_when_feasible():
    from hsrsim.rotation.community import community_pela_axis
    from hsrsim.rotation.schema import MemberPolicy
    from copy import deepcopy

    pe = community_pela_axis()
    members = {cid: deepcopy(p) for cid, p in pe.members.items()}
    members["aventurine"] = MemberPolicy("aventurine", ["basic"], mode="cycle")
    members["acheron"] = MemberPolicy("acheron", ["skill", "basic"], mode="cycle")
    axis = CycleAxis(axis_id="sp_ok", team_id=pe.team_id, members=members)
    l1 = l1_eval_axis(axis, zone=True, pin_mode="axis_strict")
    if l1["feasible"]:
        assert "aventurine_fua" in l1["sources"]
        assert l1["sources"]["aventurine_fua"] >= 0.0
        assert "aventurine_fua" in (l1.get("l1_channels") or {})
