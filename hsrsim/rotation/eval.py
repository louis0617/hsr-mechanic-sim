"""L1 evaluate a CycleAxis; L2 play it; source-gap reconcile."""
from __future__ import annotations

from collections import defaultdict

from hsrsim.analytic.adapter import build_l1_input, refine_l1_e13
from hsrsim.analytic.aventurine_l1 import (
    dream_external_from_fua,
    expected_blind_bet_gain_rate,
)
from hsrsim.analytic.coverage import iterate_state
from hsrsim.analytic.flow_model import sp_other_from_initial
from hsrsim.rotation.policy_bot import AxisSPInfeasible, build_axis_bots
from hsrsim.rotation.schema import CycleAxis
from hsrsim.simulator.av import moc_cycle_av_cap
from hsrsim.simulator.bots import GreedyBot
from hsrsim.simulator.combat_rules import CombatRules
from hsrsim.simulator.engine import Engine
from hsrsim.simulator.types import ActionType
from hsrsim.teams.loader import load_team_spec

SOURCE_KEYS = (
    "acheron_ult",
    "acheron_skill",
    "acheron_basic",
    "jiaoqiu_dot",
    "aventurine_fua",
    "other_members",
)
GATE = 0.03
RATE_KEY = {"basic": "nb", "skill": "ns", "ult": "u"}
DUMMY_SPEED = 95.0


def _infeasible(shares, sp0, dropped) -> dict:
    return {
        "dpr": 0.0,
        "rates": {},
        "sources": {k: 0.0 for k in SOURCE_KEYS},
        "skill_shares": shares,
        "feasible": False,
        "sp_other": None,
        "sp_other_start": sp0,
        "dropped_share_pins": dropped,
        "l1_channels": {},
    }


def _attach_follow_up_contexts(inp, spec) -> None:
    from hsrsim.analytic.adapter import _action_contexts

    enemy = inp.enemy
    if enemy is None:
        return
    weaknesses = [
        w.value if hasattr(w, "value") else str(w) for w in (getattr(enemy, "weaknesses", None) or [])
    ]
    allies = list(spec.scenario.allies)
    for ally in allies:
        for action in ally.build.actions:
            if action.type != ActionType.FOLLOW_UP or not action.damage_instances:
                continue
            inp.action_contexts.setdefault(ally.id, {})["follow_up"] = _action_contexts(
                ally,
                action,
                weaknesses,
                enemy=enemy,
                broken_uptime=0.0,
                allies=allies,
                action_key="skill",
            )


def _follow_up_expected(inp, state, cid: str) -> float:
    from hsrsim.analytic.coverage import _continuous_vuln_buff, expected_combo_damage

    ctxs = ((inp.action_contexts.get(cid) or {}).get("follow_up")) or []
    if not ctxs:
        return 0.0
    stacks = {s: st.stacks for s, st in (state.stack_vulns or {}).items()}
    cipher = {s: st.cipher_coverage for s, st in (state.stack_vulns or {}).items()}
    continuous = _continuous_vuln_buff(list(inp.stack_vulns or []), stacks, cipher)
    by_recipient: dict[str, list] = {}
    for buff in inp.buffs or []:
        if buff.has_zone_shifts():
            by_recipient.setdefault(buff.recipient_id, []).append(buff)
    action_buffs = []
    for b in by_recipient.get(cid, []):
        applies = getattr(b, "applies_to", None)
        if applies is None or "skill" in applies or "follow_up" in applies:
            action_buffs.append(b)
    zone_buffs = [b.as_zone_buff() for b in action_buffs]
    cov = {b.id: float(state.coverages.get(b.id, 0.0)) for b in action_buffs}
    total = 0.0
    for ctx in ctxs:
        total += float(expected_combo_damage(ctx, zone_buffs, cov, continuous=continuous).damage)
    return total


def pin_shares(inp, shares: dict[str, float]) -> dict[str, dict[str, float]]:
    """Legacy absolute pin (conflicts with extra_turns). Prefer skill_shares."""
    fixed = {}
    for ch in inp.characters:
        t = float(ch.speed) / 100.0
        r = float(shares.get(ch.id, 0.0))
        r = min(1.0, max(0.0, r))
        fixed[ch.id] = {"basic": (1.0 - r) * t, "skill": r * t}
    return fixed


def _iterate_axis(inp, shares: dict[str, float], *, sp_other: float, apply_advance: bool):
    return iterate_state(
        list(inp.characters),
        buffs=list(inp.buffs or []),
        advances=list(inp.advances or []),
        stack_vulns=list(inp.stack_vulns or []),
        action_contexts=inp.action_contexts,
        counters=list(inp.counters or []),
        skill_shares=shares,
        apply_advance=apply_advance,
        apply_overflow=True,
        sp_other=sp_other,
        dot_streams=list(getattr(inp, "dot_streams", None) or []),
    )


def _min_feasible_sp_other(inp, shares: dict[str, float], *, lo: float, hi: float = 8.0) -> float | None:
    best = None
    a, b = float(lo), float(hi)
    for _ in range(28):
        mid = 0.5 * (a + b)
        try:
            _iterate_axis(inp, shares, sp_other=mid, apply_advance=True)
            b = mid
            best = mid
        except RuntimeError:
            a = mid
    return best


def l1_eval_axis(
    axis: CycleAxis,
    *,
    zone: bool,
    ult_timing: str | None = None,
    av_clock: float | None = None,
    speed_override: dict[str, float] | None = None,
    broken_uptime: float = 0.0,
    hits_per_100_av: float = 0.0,
    energy_hit: float = 0.0,
    share_override: dict[str, float] | None = None,
    pin_mode: str = "axis_strict",
) -> dict:
    inp = build_l1_input(
        axis.team_id,
        energy_hit=energy_hit,
        hits_per_100_av=hits_per_100_av,
        acheron_ult_e11h=True,
        broken_uptime=broken_uptime,
        speed_override=speed_override,
    )
    spec = load_team_spec(axis.team_id, speed_override=speed_override)
    _attach_follow_up_contexts(inp, spec)
    shares = dict(share_override) if share_override is not None else axis.skill_shares()
    av = float(av_clock) if av_clock is not None else moc_cycle_av_cap(100)
    sp0 = sp_other_from_initial(3.0, av)
    sp_need = _min_feasible_sp_other(inp, shares, lo=0.0)
    dropped: list[str] = []
    if pin_mode == "axis" and (sp_need is None or sp_need > sp0 + 1e-9):
        trial = dict(shares)
        for cid in ("aventurine", "jiaoqiu", "pela", "acheron"):
            if cid not in trial:
                continue
            del trial[cid]
            dropped.append(cid)
            sp_need = _min_feasible_sp_other(inp, trial, lo=0.0)
            if sp_need is not None and sp_need <= sp0 + 1e-9:
                shares = trial
                break
        else:
            return _infeasible(shares, sp0, dropped)
        sp_used = sp0
    elif pin_mode == "axis_strict":
        if sp_need is None or sp_need > sp0 + 1e-9:
            return {
                **_infeasible(shares, sp0, dropped),
                "sp_need": sp_need,
                "pin_mode": pin_mode,
            }
        sp_used = sp0
    else:
        if sp_need is None:
            return _infeasible(shares, sp0, dropped)
        sp_used = max(sp0, sp_need)
    state = _iterate_axis(inp, shares, sp_other=sp_used, apply_advance=True)
    rates = {cid: dict(v) for cid, v in state.solution.rates.items()}
    aven_u = float(rates.get("aventurine", {}).get("u", 0.0))
    fua_est = expected_blind_bet_gain_rate(
        list(spec.scenario.allies),
        enemy_speed=DUMMY_SPEED,
        aventurine_ult_rate=aven_u,
        chip_uptime=1.0,
    )
    fua_dream = dream_external_from_fua(fua_est["fua_rate"], has_lc_23023=True)
    timing = ult_timing or axis.acheron_ult()
    inp2, meta = refine_l1_e13(
        inp,
        rates,
        panel_chars=list(spec.scenario.allies),
        zone_proc_counts=zone,
        ult_timing=timing,
        fua_dream_external=fua_dream,
    )
    _attach_follow_up_contexts(inp2, spec)
    state2 = _iterate_axis(inp2, shares, sp_other=sp_used, apply_advance=True)
    sources, l1_channels = _l1_sources(inp2, state2, fua_rate=float(fua_est["fua_rate"]))
    combo_team = sum(float(sources[k]) for k in SOURCE_KEYS)
    return {
        "dpr": combo_team,
        "lp_dpr": float(state2.solution.total_damage),
        "rates": {cid: dict(v) for cid, v in state2.solution.rates.items()},
        "sources": sources,
        "l1_channels": l1_channels,
        "fua_rate": float(fua_est["fua_rate"]),
        "zone_meta": {k: meta.get(k) for k in ("zone_cov", "sparkle_at_ult_cov") if k in meta},
        "skill_shares": shares,
        "feasible": True,
        "converged": bool(state2.converged),
        "sp_other": sp_used,
        "sp_other_start": sp0,
        "sp_deficit": max(0.0, sp_used - sp0),
        "dropped_share_pins": dropped,
        "pin_mode": pin_mode,
        "shared_inputs": {
            "broken_uptime": broken_uptime,
            "hits_per_100_av": hits_per_100_av,
            "energy_hit": energy_hit,
            "av_clock": av,
            "sparkle_at_ult_cov": meta.get("sparkle_at_ult_cov"),
            "skill_shares": "axis definition (not L2 measured)",
        },
        "note": (
            "轴 L1：份额由轴定义；ns=r(spd/100+extra)；拉条进不动点。"
            "axis_strict：SP 不足则不可行，不抬 sp_other、不解钉。"
            "含砂金追击通道（盲注速率 × 天赋追击期望伤害）。"
        ),
    }


def _l1_sources(inp, state, *, fua_rate: float = 0.0) -> tuple[dict[str, float], dict[str, float]]:
    from scripts.report_d1_8 import l1_per_action_expected

    rates = state.solution.rates
    exp = l1_per_action_expected(inp, state)

    def contrib(cid: str, action: str) -> float:
        if cid not in rates:
            return 0.0
        rate = float(rates[cid][RATE_KEY[action]])
        segs = (exp.get(cid) or {}).get(action) or []
        mean = sum(float(s["expected_damage"]) for s in segs)
        return rate * mean

    channels: dict[str, float] = {}
    ult = contrib("acheron", "ult")
    skill = contrib("acheron", "skill")
    basic = contrib("acheron", "basic")
    channels["acheron_ult"] = ult
    channels["acheron_skill"] = skill
    channels["acheron_basic"] = basic
    jq_dot = float(state.dot_damage_per_100_av or 0.0)
    channels["jiaoqiu_dot"] = jq_dot
    fua = float(fua_rate) * _follow_up_expected(inp, state, "aventurine")
    channels["aventurine_fua"] = fua
    other = 0.0
    for cid in rates:
        if cid == "acheron":
            continue
        for action in ("basic", "skill", "ult"):
            dmg = contrib(cid, action)
            channels[f"{cid}_{action}"] = dmg
            other += dmg
    sources = {
        "acheron_ult": ult,
        "acheron_skill": skill,
        "acheron_basic": basic,
        "jiaoqiu_dot": jq_dot,
        "aventurine_fua": fua,
        "other_members": other,
    }
    return sources, channels


def _l2_channel_key(att: str, aid: str, dtype: str) -> str | None:
    if att.startswith("lv") or "dummy" in att or att == "enemy":
        return None
    if att == "acheron" and "ult" in aid:
        return "acheron_ult"
    if att == "acheron" and "skill" in aid:
        return "acheron_skill"
    if att == "acheron" and "basic" in aid:
        return "acheron_basic"
    if aid == "aventurine_fua" or (att == "aventurine" and "fua" in aid):
        return "aventurine_fua"
    if "ashen" in aid or (att == "jiaoqiu" and (dtype == "dot" or aid.startswith("dot:"))):
        return "jiaoqiu_dot"
    if "basic" in aid:
        return f"{att}_basic"
    if "skill" in aid:
        return f"{att}_skill"
    if "ult" in aid:
        return f"{att}_ult"
    return f"{att}:{aid}"


def play_axis_l2(
    axis: CycleAxis,
    *,
    zone: bool,
    max_cycles: int = 100,
    seed: int = 42,
    speed_override: dict[str, float] | None = None,
    strict_sp: bool = True,
    keep_engine: bool = True,
    n_enemies: int | None = None,
    mortenax_zone_dream: bool = True,
    mortenax_sensitivity_full_heal: bool = False,
    jiaoqiu_trace_1218102: bool = True,
    sparkle_trace_11306102: bool = True,
) -> dict:
    if any(getattr(p, "mode", "cycle") == "adaptive" for p in axis.members.values()):
        strict_sp = False
    spec = load_team_spec(
        axis.team_id, speed_override=speed_override, n_enemies=n_enemies
    )
    scenario = spec.scenario.model_copy(deep=True)
    scenario.max_cycles = max_cycles
    for enemy in scenario.enemies:
        enemy.immortal = True
    bots = build_axis_bots(scenario, axis, strict_sp=strict_sp)
    eng = Engine(
        scenario,
        ally_bots=bots,
        enemy_bot=GreedyBot(),
        random_seed=seed,
        combat_rules=CombatRules(
            zone_proc_counts_for_dream=zone,
            acheron_ult_e11h=True,
            followup_counts_as_action=True,
            mortenax_zone_debuff_counts_for_dream=mortenax_zone_dream,
            mortenax_sensitivity_full_heal=mortenax_sensitivity_full_heal,
            jiaoqiu_trace_1218102=jiaoqiu_trace_1218102,
            sparkle_trace_11306102=sparkle_trace_11306102,
        ),
    )
    try:
        result = eng.run()
    except AxisSPInfeasible as exc:
        return {
            "feasible": False,
            "reason": str(exc),
            "dpr": None,
            "av": float(eng.state.av_clock) if eng.state else None,
            "engine": eng if keep_engine else None,
            "sources": {k: 0.0 for k in SOURCE_KEYS},
            "l2_channels": {},
        }
    av = float(eng.state.av_clock) or 1.0
    dpr = float(eng.state.total_damage_dealt) * (100.0 / av)
    by_aid: dict[str, float] = defaultdict(float)
    channels: dict[str, float] = defaultdict(float)
    scale = 100.0 / av
    sources = {k: 0.0 for k in SOURCE_KEYS}
    for ev in eng.events:
        if ev.event_type != "damage":
            continue
        p = ev.payload or {}
        aid = str(p.get("action") or "")
        dmg_raw = float(p.get("raw_damage") or 0.0)
        by_aid[aid] += dmg_raw
        dmg = dmg_raw * scale
        att = str(p.get("attacker") or "")
        dtype = str(p.get("damage_type") or "")
        ch = _l2_channel_key(att, aid, dtype)
        if ch:
            channels[ch] += dmg
        if ch in SOURCE_KEYS:
            sources[ch] += dmg
        elif ch is None:
            continue
        else:
            sources["other_members"] += dmg
    accounted = sum(sources[k] for k in SOURCE_KEYS)
    drift = dpr - accounted
    sources["other_members"] += drift
    return {
        "feasible": True,
        "dpr": dpr,
        "av": av,
        "engine": eng if keep_engine else None,
        "result": result,
        "sources": sources,
        "l2_channels": dict(channels),
        "damage_by_action": {k: v * scale for k, v in by_aid.items()},
    }


def play_greedy_l2(
    team_id: str,
    *,
    zone: bool,
    max_cycles: int = 100,
    seed: int = 42,
    speed_override: dict[str, float] | None = None,
) -> dict:
    from hsrsim.simulator.bots import build_team_evaluation_bots
    from hsrsim.simulator.ult_timing import UltTimingStrategy

    spec = load_team_spec(team_id, speed_override=speed_override)
    scenario = spec.scenario.model_copy(deep=True)
    scenario.max_cycles = max_cycles
    for enemy in scenario.enemies:
        enemy.immortal = True
    bots = build_team_evaluation_bots(
        scenario,
        spec.main_dps,
        main_bot=GreedyBot(),
        ult_timing=UltTimingStrategy.IMMEDIATE_WHEN_FULL.value,
    )
    eng = Engine(
        scenario,
        ally_bots=bots,
        enemy_bot=GreedyBot(),
        random_seed=seed,
        combat_rules=CombatRules(
            zone_proc_counts_for_dream=zone,
            acheron_ult_e11h=True,
            followup_counts_as_action=True,
        ),
    )
    eng.run()
    av = float(eng.state.av_clock) or 1.0
    return {"dpr": float(eng.state.total_damage_dealt) * (100.0 / av), "engine": eng}


def channel_coverage_check(
    l2_channels: dict[str, float], l1_channels: dict[str, float], *, eps: float = 1.0
) -> dict:
    """Every L2 damage channel with mass must have a corresponding L1 term."""
    missing = []
    for ch, dmg in l2_channels.items():
        if dmg < eps:
            continue
        if ch not in l1_channels:
            missing.append(ch)
    return {
        "pass": not missing,
        "missing_in_l1": missing,
        "l2_channels": sorted(k for k, v in l2_channels.items() if v >= eps),
        "l1_channels": sorted(l1_channels),
    }


def reconcile_axis(
    axis: CycleAxis,
    *,
    zone: bool,
    max_cycles: int = 100,
    speed_override: dict[str, float] | None = None,
) -> dict:
    l2 = play_axis_l2(axis, zone=zone, max_cycles=max_cycles, speed_override=speed_override)
    if not l2.get("feasible"):
        l1 = l1_eval_axis(axis, zone=zone, speed_override=speed_override, pin_mode="axis_strict")
        return {
            "axis_id": axis.axis_id,
            "zone": zone,
            "speed_override": speed_override,
            "l2_feasible": False,
            "l1_feasible": bool(l1.get("feasible")),
            "gate_pass": False,
            "source_fails": ["l2_sp_infeasible"],
            "note": l2.get("reason"),
            "l1_dpr": l1.get("dpr"),
            "l2_dpr": None,
        }
    eng = l2["engine"]
    hits = sum(1 for e in eng.events if e.event_type == "ally_hit")
    scale = 100.0 / float(l2["av"])
    l1 = l1_eval_axis(
        axis,
        zone=zone,
        av_clock=l2["av"],
        speed_override=speed_override,
        broken_uptime=float(eng.broken_uptime()),
        hits_per_100_av=float(hits) * scale,
        pin_mode="axis_strict",
    )
    ch_chk = channel_coverage_check(l2.get("l2_channels") or {}, l1.get("l1_channels") or {})
    if not l1.get("feasible"):
        return {
            "axis_id": axis.axis_id,
            "zone": zone,
            "speed_override": speed_override,
            "l1_feasible": False,
            "l2_feasible": True,
            "l1_dpr": 0.0,
            "l2_dpr": l2["dpr"],
            "gate_pass": False,
            "channel_coverage": ch_chk,
            "source_fails": ["l1_sp_infeasible"],
            "shared_inputs": {
                "skill_shares": "axis definition (not L2 measured)",
                "broken_uptime": float(eng.broken_uptime()),
            },
            "note": "L1 轴份额 SP 不可行；不对账。",
        }
    l1_team = float(l1["dpr"])
    l2_team = float(l2["dpr"])
    signed = (l1_team - l2_team) / l2_team if l2_team else None
    per: dict[str, dict] = {}
    abs_sum = 0.0
    fails: list[str] = []
    for key in SOURCE_KEYS:
        a = float(l1["sources"].get(key) or 0.0)
        b = float(l2["sources"].get(key) or 0.0)
        rel = None if abs(b) < 1e-9 else (a - b) / b
        ok = True if abs(b) < 1e-9 else abs(rel) <= GATE
        per[key] = {"l1": a, "l2": b, "rel": rel, "pass": ok}
        abs_sum += abs(a - b)
        if not ok:
            fails.append(key)
    sigma_ok = (abs_sum / l2_team <= GATE) if l2_team else False
    team_ok = abs(signed or 99) <= GATE
    return {
        "axis_id": axis.axis_id,
        "zone": zone,
        "speed_override": speed_override,
        "l1_feasible": True,
        "l2_feasible": True,
        "l1_dpr": l1_team,
        "l2_dpr": l2_team,
        "team_rel": signed,
        "team_pass": team_ok,
        "source_pass": not fails,
        "sigma_pass": sigma_ok,
        "channel_coverage": ch_chk,
        "gate_pass": (
            bool(l1.get("feasible"))
            and team_ok
            and (not fails)
            and sigma_ok
            and bool(ch_chk.get("pass"))
        ),
        "source_fails": fails,
        "sources": per,
        "l2_av": l2["av"],
        "shared_inputs": {
            **(l1.get("shared_inputs") or {}),
            "share_pin": "axis definition (not L2 measured)",
        },
        "note": (
            "份额由轴定义 + 拉条。broken_uptime / 受击能量速率来自同局 L2（共享输入）。"
            "门闩含 L2 伤害通道须在 L1 有对应项。门槛 3% 不调。不阻塞 F 出口。"
        ),
    }
