"""Local search around a start CycleAxis. L1 scores; top-k L2 verify. No shield term."""
from __future__ import annotations

from copy import deepcopy

from hsrsim.rotation.eval import l1_eval_axis, play_axis_l2, play_greedy_l2
from hsrsim.rotation.schema import CycleAxis, MemberPolicy, pattern_distance


def _clone_with(axis: CycleAxis, **member_cycles: list[str] | str) -> CycleAxis:
    members = {cid: deepcopy(p) for cid, p in axis.members.items()}
    for key, val in member_cycles.items():
        if key == "acheron_ult":
            members["acheron"].ult = str(val)  # type: ignore[assignment]
            continue
        if key in members and isinstance(val, list):
            members[key] = MemberPolicy(
                actor_id=members[key].actor_id,
                normal_cycle=list(val),
                ult=members[key].ult,
                default_target=members[key].default_target,
            )
    return CycleAxis(
        axis_id=axis.axis_id + "|mut",
        team_id=axis.team_id,
        members=members,
        sources=list(axis.sources),
        notes="local search neighbor",
    )


def neighbors(start: CycleAxis) -> list[CycleAxis]:
    out = [start]
    if "jiaoqiu" in start.members:
        for cyc in (
            ["skill", "basic", "basic"],
            ["basic", "basic", "skill"],
            ["skill"],
            ["basic"],
            ["skill", "skill", "basic"],
        ):
            out.append(_clone_with(start, jiaoqiu=cyc))
    if "pela" in start.members:
        for cyc in (["skill"], ["skill", "basic"], ["basic"]):
            out.append(_clone_with(start, pela=cyc))
    if "aventurine" in start.members:
        for cyc in (["skill"], ["basic"], ["skill", "basic"]):
            out.append(_clone_with(start, aventurine=cyc))
    if "acheron" in start.members:
        out.append(_clone_with(start, acheron=["skill"]))
        out.append(_clone_with(start, acheron=["skill", "basic"]))
        out.append(_clone_with(start, acheron_ult="wait_sparkle_skill_buff"))
        out.append(_clone_with(start, acheron_ult="immediate_when_full"))
    # unique by pattern
    seen: set[tuple] = set()
    uniq: list[CycleAxis] = []
    for ax in out:
        key = tuple(
            (cid, tuple(p.normal_cycle), p.ult) for cid, p in sorted(ax.members.items())
        )
        if key in seen:
            continue
        seen.add(key)
        uniq.append(ax)
    return uniq


def search_axis(
    start: CycleAxis,
    *,
    zone: bool,
    top_k: int = 4,
    l2_cycles: int = 100,
    greedy_ref: float | None = None,
    allow_l1_rank: bool = False,
    speed_override: dict[str, float] | None = None,
) -> dict:
    """Local search. L1 ranking is off until community-axis L1/L2 gate_pass."""
    cands = neighbors(start)
    scored = []
    if allow_l1_rank:
        for ax in cands:
            try:
                l1 = l1_eval_axis(ax, zone=zone, speed_override=speed_override)
            except Exception as exc:  # noqa: BLE001
                scored.append((float("-inf"), ax, {"error": str(exc), "feasible": False}))
                continue
            if not l1.get("feasible"):
                scored.append((float("-inf"), ax, l1))
            else:
                scored.append((float(l1["dpr"]), ax, l1))
        scored.sort(key=lambda t: t[0], reverse=True)
        to_verify = [t for t in scored if t[0] != float("-inf")][:top_k]
        rank_mode = "l1_then_l2"
    else:
        rank_mode = "l2_only"
        to_verify = [(0.0, ax, {}) for ax in cands]
    verified = []
    for l1_dpr, ax, l1 in to_verify:
        l2 = play_axis_l2(
            ax, zone=zone, max_cycles=l2_cycles, speed_override=speed_override
        )
        if not l2.get("feasible", True) or l2.get("dpr") is None:
            continue
        verified.append(
            {
                "l1_dpr": l1_dpr if allow_l1_rank else None,
                "l2_dpr": l2["dpr"],
                "axis": ax.to_dict(),
                "pattern_vs_start": pattern_distance(start, ax),
            }
        )
    verified.sort(key=lambda r: r["l2_dpr"], reverse=True)
    best = verified[0] if verified else None
    greedy = greedy_ref
    if greedy is None:
        greedy = play_greedy_l2(
            start.team_id,
            zone=zone,
            max_cycles=l2_cycles,
            speed_override=speed_override,
        )["dpr"]
    start_row = play_axis_l2(
        start, zone=zone, max_cycles=l2_cycles, speed_override=speed_override
    )
    start_l2 = start_row.get("dpr") if start_row.get("feasible", True) else None
    return {
        "n_neighbors": len(cands),
        "rank_mode": rank_mode,
        "allow_l1_rank": allow_l1_rank,
        "l2_cycles": l2_cycles,
        "verified": verified,
        "best": best,
        "start_l2_dpr": start_l2,
        "greedy_l2_dpr": greedy,
        "best_vs_start": (
            (best["l2_dpr"] - start_l2) / start_l2 if best and start_l2 else None
        ),
        "best_vs_greedy": (
            (best["l2_dpr"] - greedy) / greedy if best and greedy else None
        ),
    }
