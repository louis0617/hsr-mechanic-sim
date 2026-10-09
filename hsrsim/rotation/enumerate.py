"""L2 pattern enumeration for F batch 3. No SP fallback; infeasible axes skipped."""
from __future__ import annotations

from itertools import product

from hsrsim.rotation.schema import CycleAxis, MemberPolicy, f6_key, pattern_label

PATTERNS: dict[str, list[str]] = {
    "全E": ["skill"],
    "EA": ["skill", "basic"],
    "EAA": ["skill", "basic", "basic"],
    "EEA": ["skill", "skill", "basic"],
    "全A": ["basic"],
}
# 花火社区「一直 E」；黄泉「尽量战技」仍扫满五模式以免漏 SP 紧轴。
SPARKLE_LABELS = ("全E",)
ACHERON_LABELS = ("全E", "EA", "EAA", "EEA", "全A")
SUPPORT_LABELS = ("全E", "EA", "EAA", "EEA", "全A")
AVEN_LABELS = SUPPORT_LABELS
ULTS = ("immediate_when_full", "wait_sparkle_skill_buff")


def _mem(cid: str, label: str, *, ult: str = "immediate_when_full", target: str | None = None) -> MemberPolicy:
    return MemberPolicy(cid, list(PATTERNS[label]), ult=ult, default_target=target)


PRIORITY_ORDERS = {
    "community": None,  # filled per team
    "speed_card": None,
}


def _priority(team: str, name: str) -> list[str]:
    if team == "jq":
        if name == "community":
            return ["sparkle", "acheron", "jiaoqiu", "aventurine"]
        return ["sparkle", "jiaoqiu", "aventurine", "acheron"]
    if team == "mort":
        if name == "community":
            return ["sparkle", "acheron", "mortenaxblade", "aventurine"]
        return ["sparkle", "mortenaxblade", "aventurine", "acheron"]
    if team == "mort_jq":
        if name == "community":
            return ["acheron", "jiaoqiu", "mortenaxblade", "aventurine"]
        return ["jiaoqiu", "mortenaxblade", "aventurine", "acheron"]
    if name == "community":
        return ["sparkle", "acheron", "pela", "aventurine"]
    return ["sparkle", "pela", "aventurine", "acheron"]


def _adaptive_mem(cid: str, k: int, *, ult="immediate_when_full", target=None) -> MemberPolicy:
    return MemberPolicy(
        cid,
        ["basic"] if k >= 99 else ["skill"],
        ult=ult,
        default_target=target,
        mode="adaptive",
        sp_min=k,
    )


def iter_adaptive_axes(team: str) -> list[CycleAxis]:
    """k ∈ {1,2,never} for support/acheron/aven; sparkle k=1; 2 priority orders; 2 ults."""
    if team == "jq":
        team_id = "acheron_direct"
        supp = "jiaoqiu"
    elif team == "pela":
        team_id = "acheron_old_pela_res"
        supp = "pela"
    elif team == "mort":
        team_id = "acheron_mortenax"
        ks = (1, 2, 99)
        out: list[CycleAxis] = []
        for ac_k, av_k, pri_name, ult in product(ks, ks, ("community", "speed_card"), ULTS):
            pri = _priority(team, pri_name)
            members = {
                "sparkle": _adaptive_mem("sparkle", 1, target="acheron"),
                "mortenaxblade": _adaptive_mem("mortenaxblade", 1),
                "acheron": _adaptive_mem("acheron", ac_k, ult=ult),
                "aventurine": _adaptive_mem("aventurine", av_k),
            }
            axis_id = f"{team}|ad|ac{ac_k}|av{av_k}|{pri_name}|{ult}"
            out.append(
                CycleAxis(
                    axis_id=axis_id,
                    team_id=team_id,
                    members=members,
                    sp_priority=pri,
                    notes=f"adaptive {pri_name} mortenax skill 0 SP",
                )
            )
        return out
    elif team == "mort_jq":
        team_id = "acheron_mortenax_jq"
        ks = (1, 2, 99)
        out = []
        for su_k, ac_k, av_k, pri_name, ult in product(ks, ks, ks, ("community", "speed_card"), ULTS):
            pri = _priority(team, pri_name)
            members = {
                "jiaoqiu": _adaptive_mem("jiaoqiu", su_k),
                "mortenaxblade": _adaptive_mem("mortenaxblade", 1),
                "acheron": _adaptive_mem("acheron", ac_k, ult=ult),
                "aventurine": _adaptive_mem("aventurine", av_k),
            }
            axis_id = f"{team}|ad|su{su_k}|ac{ac_k}|av{av_k}|{pri_name}|{ult}"
            out.append(
                CycleAxis(
                    axis_id=axis_id,
                    team_id=team_id,
                    members=members,
                    sp_priority=pri,
                    notes=f"adaptive {pri_name} no sparkle",
                )
            )
        return out
    else:
        raise ValueError(team)
    ks = (1, 2, 99)
    out: list[CycleAxis] = []
    for su_k, ac_k, av_k, pri_name, ult in product(ks, ks, ks, ("community", "speed_card"), ULTS):
        pri = _priority(team, pri_name)
        members = {
            "sparkle": _adaptive_mem("sparkle", 1, target="acheron"),
            supp: _adaptive_mem(supp, su_k),
            "acheron": _adaptive_mem("acheron", ac_k, ult=ult),
            "aventurine": _adaptive_mem("aventurine", av_k),
        }
        axis_id = f"{team}|ad|su{su_k}|ac{ac_k}|av{av_k}|{pri_name}|{ult}"
        out.append(
            CycleAxis(
                axis_id=axis_id,
                team_id=team_id,
                members=members,
                sp_priority=pri,
                notes=f"adaptive {pri_name}",
            )
        )
    return out


def iter_axes(team: str) -> list[CycleAxis]:
    """Enumerate legal CycleAxis objects (not yet L2-played)."""
    if team == "jq":
        team_id = "acheron_direct"
        supp_id = "jiaoqiu"
    elif team == "pela":
        team_id = "acheron_old_pela_res"
        supp_id = "pela"
    else:
        raise ValueError(team)
    out: list[CycleAxis] = []
    for sp, su, ac, av, ult in product(
        SPARKLE_LABELS, SUPPORT_LABELS, ACHERON_LABELS, AVEN_LABELS, ULTS
    ):
        members = {
            "sparkle": _mem("sparkle", sp, target="acheron"),
            supp_id: _mem(supp_id, su),
            "acheron": _mem("acheron", ac, ult=ult),
            "aventurine": _mem("aventurine", av),
        }
        axis_id = f"{team}|sp{sp}|{supp_id}{su}|ac{ac}|av{av}|{ult}"
        out.append(
            CycleAxis(
                axis_id=axis_id,
                team_id=team_id,
                members=members,
                notes="F3 穷举候选",
            )
        )
    return out


def summarize_leaderboard(
    rows: list[dict],
    community: CycleAxis,
    *,
    top_n: int = 10,
) -> dict:
    """rows: feasible plays with l2_dpr. Rank global + F6 community neighborhood."""
    feas = [r for r in rows if r.get("feasible")]
    feas.sort(key=lambda r: float(r["l2_dpr"]), reverse=True)
    best = feas[0] if feas else None
    best_dpr = float(best["l2_dpr"]) if best else None

    def gap(dpr: float) -> float | None:
        if best_dpr is None or best_dpr == 0:
            return None
        return (dpr - best_dpr) / best_dpr

    top = []
    for i, r in enumerate(feas[:top_n], start=1):
        top.append(
            {
                "rank": i,
                "axis_id": r["axis_id"],
                "l2_dpr": r["l2_dpr"],
                "gap_vs_best": gap(float(r["l2_dpr"])),
                "labels": r.get("labels"),
            }
        )

    comm_id = community.axis_id
    comm_rank = None
    comm_row = None
    for i, r in enumerate(feas, start=1):
        if r.get("community_exact"):
            comm_rank = i
            comm_row = r
            break
    if comm_row is None:
        ck = f6_key(community)
        for i, r in enumerate(feas, start=1):
            if r.get("f6_key") == ck and r.get("aven_label") == (
                f"ad k={community.members['aventurine'].sp_min}"
                if community.members["aventurine"].mode == "adaptive"
                else pattern_label(community.members["aventurine"].normal_cycle)
            ):
                comm_rank = i
                comm_row = r
                break

    ck = f6_key(community)
    f6_matches = [r for r in feas if r.get("f6_key") == ck]
    f6_best = f6_matches[0] if f6_matches else None
    f6_rank = None
    if f6_best is not None:
        for i, r in enumerate(feas, start=1):
            if r["axis_id"] == f6_best["axis_id"]:
                f6_rank = i
                break

    return {
        "n_total": len(rows),
        "n_feasible": len(feas),
        "n_infeasible": sum(1 for r in rows if not r.get("feasible")),
        "best": None
        if best is None
        else {
            "axis_id": best["axis_id"],
            "l2_dpr": best["l2_dpr"],
            "labels": best.get("labels"),
            "axis": best.get("axis"),
        },
        "top10": top,
        "community_exact": {
            "axis_id": comm_id,
            "rank": comm_rank,
            "l2_dpr": None if comm_row is None else comm_row["l2_dpr"],
            "gap_vs_best": None if comm_row is None else gap(float(comm_row["l2_dpr"])),
            "note": "精确社区编码（含砂金循环）",
        },
        "community_f6": {
            "rank_of_best_matching_f6": f6_rank,
            "n_matching": len(f6_matches),
            "best_l2_dpr": None if f6_best is None else f6_best["l2_dpr"],
            "gap_vs_best": None if f6_best is None else gap(float(f6_best["l2_dpr"])),
            "best_aven_label": None if f6_best is None else f6_best.get("aven_label"),
            "note": (
                "F6：只比花火/椒丘或佩拉/黄泉循环与黄泉开大。"
                "砂金差异 = 纸面最大值 vs 实战生存，不计入社区轴是否『附近』的循环判定。"
            ),
        },
    }
