"""F close: adaptive SP policies in enum + ult sparkle coverage at guide speed."""
from __future__ import annotations

import json
from pathlib import Path

from hsrsim.rotation.community import (
    GUIDE_SPEED_JQ,
    GUIDE_SPEED_PELA,
    community_jq_axis,
    community_pela_axis,
)
from hsrsim.rotation.coverage import attach_live_sampler, summarize_snaps
from hsrsim.rotation.enumerate import iter_adaptive_axes, summarize_leaderboard
from hsrsim.rotation.eval import play_axis_l2
from hsrsim.rotation.schema import CycleAxis, f6_key

REPO = Path(__file__).resolve().parents[1]
OUT_MD = REPO / "docs" / "V1.0" / "F_CLOSE_REPORT.md"
OUT_JSON = REPO / "docs" / "V1.0" / "F_CLOSE_RESULTS.json"
CYCLES = 100


def _labels(axis: CycleAxis) -> dict:
    out = {}
    for cid, p in axis.members.items():
        if p.mode == "adaptive":
            out[cid] = f"ad k={p.sp_min}"
        else:
            from hsrsim.rotation.schema import pattern_label

            out[cid] = pattern_label(p.normal_cycle)
    out["acheron_ult"] = axis.acheron_ult()
    out["priority"] = list(axis.sp_priority)
    return out


def _row(axis: CycleAxis, l2: dict, comm: CycleAxis) -> dict:
    return {
        "axis_id": axis.axis_id,
        "feasible": bool(l2.get("feasible")),
        "reason": l2.get("reason"),
        "l2_dpr": l2.get("dpr"),
        "labels": _labels(axis),
        "aven_label": _labels(axis).get("aventurine"),
        "f6_key": f6_key(axis),
        "community_exact": axis.axis_id == comm.axis_id
        or (
            f6_key(axis) == f6_key(comm)
            and tuple(axis.sp_priority) == tuple(comm.sp_priority)
            and axis.members["aventurine"].mode == comm.members["aventurine"].mode
            and int(axis.members["aventurine"].sp_min) == int(comm.members["aventurine"].sp_min)
        ),
        "axis": axis.to_dict() if l2.get("feasible") else None,
        "family": "adaptive" if any(p.mode == "adaptive" for p in axis.members.values()) else "cycle",
    }


def play_many(axes, comm, *, zone, speed, tag):
    rows = []
    n = len(axes)
    for i, axis in enumerate(axes, start=1):
        if i == 1 or i % 40 == 0 or i == n:
            print(f"  {tag} {i}/{n}", flush=True)
        l2 = play_axis_l2(
            axis,
            zone=zone,
            max_cycles=CYCLES,
            speed_override=speed,
            keep_engine=False,
        )
        rows.append(_row(axis, l2, comm))
    return rows


def ult_sparkle_cov(axis: CycleAxis, *, zone: bool, speed) -> dict:
    from hsrsim.rotation.eval import play_axis_l2 as play

    spec_play = play(
        axis, zone=zone, max_cycles=CYCLES, speed_override=speed, keep_engine=True
    )
    if not spec_play.get("feasible"):
        return {"feasible": False, "reason": spec_play.get("reason")}
    # sampler must attach before run — replay
    from hsrsim.teams.loader import load_team_spec
    from hsrsim.rotation.policy_bot import build_axis_bots
    from hsrsim.simulator.bots import GreedyBot
    from hsrsim.simulator.combat_rules import CombatRules
    from hsrsim.simulator.engine import Engine

    spec = load_team_spec(axis.team_id, speed_override=speed)
    sc = spec.scenario.model_copy(deep=True)
    sc.max_cycles = CYCLES
    for e in sc.enemies:
        e.immortal = True
    eng = Engine(
        sc,
        ally_bots=build_axis_bots(sc, axis, strict_sp=False),
        enemy_bot=GreedyBot(),
        random_seed=42,
        combat_rules=CombatRules(zone_proc_counts_for_dream=zone, acheron_ult_e11h=True),
    )
    snaps = attach_live_sampler(eng)
    eng.run()
    live = summarize_snaps(snaps)
    u = live.get("ult") or {}
    cov = float(u.get("sparkle_skill") or 0.0)
    return {
        "feasible": True,
        "ult_sparkle_skill": cov,
        "n_ult": u.get("n"),
        "axis_id": axis.axis_id,
        "ult_policy": axis.acheron_ult(),
        "verdict": (
            "速度档位消除开大时机问题"
            if cov >= 0.95
            else "覆盖未饱和，须查等待策略是否生效"
        ),
    }


def pct(x):
    return "n/a" if x is None else f"{100 * x:.2f}%"


def main() -> None:
    jq = community_jq_axis()
    pe = community_pela_axis()
    boards = {}
    coverage = {}
    for team, comm, spd in (("jq", jq, GUIDE_SPEED_JQ), ("pela", pe, GUIDE_SPEED_PELA)):
        ada = iter_adaptive_axes(team)
        for zone in (True, False):
            key = f"{team}_zone_{'on' if zone else 'off'}"
            print("enum", key, flush=True)
            rows = play_many(ada, comm, zone=zone, speed=spd, tag=f"{key}-ad")
            rows += play_many([comm], comm, zone=zone, speed=spd, tag=f"{key}-comm")
            seen = set()
            uniq = []
            for r in rows:
                if r["axis_id"] in seen:
                    continue
                seen.add(r["axis_id"])
                uniq.append(r)
            b = summarize_leaderboard(uniq, comm)
            feas = [r for r in uniq if r.get("feasible")]
            feas.sort(key=lambda r: r["l2_dpr"], reverse=True)
            if feas:
                b["best"]["axis"] = feas[0]["axis"]
                b["best"]["family"] = feas[0].get("family")
            boards[key] = b

        # coverage on sequence-tied pair: community adaptive both ults + batch3-like best if present
        best_on = CycleAxis.from_dict(boards[f"{team}_zone_on"]["best"]["axis"])
        print("coverage", team, flush=True)
        coverage[team] = {
            "best_as_played": ult_sparkle_cov(best_on, zone=True, speed=spd),
        }
        for ult in ("immediate_when_full", "wait_sparkle_skill_buff"):
            ax = CycleAxis.from_dict(comm.to_dict())
            ax.members["acheron"].ult = ult
            ax.axis_id = comm.axis_id + "|" + ult
            coverage[team][ult] = ult_sparkle_cov(ax, zone=True, speed=spd)

    jq_on = boards["jq_zone_on"]["best"]["l2_dpr"]
    jq_off = boards["jq_zone_off"]["best"]["l2_dpr"]
    pe_on = boards["pela_zone_on"]["best"]["l2_dpr"]
    pe_off = boards["pela_zone_off"]["best"]["l2_dpr"]
    e15 = {
        "enum_jq_vs_pela_on": (jq_on - pe_on) / pe_on if pe_on else None,
        "enum_jq_vs_pela_off": (jq_off - pe_off) / pe_off if pe_off else None,
        "jq_on": jq_on,
        "jq_off": jq_off,
        "pela_on": pe_on,
        "pela_off": pe_off,
    }
    results = {"horizon": CYCLES, "boards": boards, "coverage": coverage, "e15_enum_best": e15}
    OUT_JSON.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    lines = [
        "# F 收尾：自适应战技点 + 开大覆盖",
        "",
        "**日期**：2026-10-06（佩拉 SPBase 入表后重跑）  ",
        "**撤回**：第三批「佩拉社区轴不在最优附近」——那是**严格序列**在 SP=0 时不可行，不是社区打法本身差。",
        "**数据**：[`F_CLOSE_RESULTS.json`](F_CLOSE_RESULTS.json)",
        "",
        "## 编码",
        "",
        "自适应：SP≥k 放战技，否则普攻。抢点：高优先级角色各预留 1 点。",
        "社区优先级：花火 ＞ 黄泉 ＞ 椒丘/佩拉 ＞ 砂金（花火一直 E、黄泉尽量战技；速度序是配速）。",
        "砂金 k=2（盈余才 E），单独标注纸面最大 vs 生存。",
        "",
    ]
    for key in ("jq_zone_on", "jq_zone_off", "pela_zone_on", "pela_zone_off"):
        b = boards[key]
        best = b["best"]
        ce = b["community_exact"]
        cf = b["community_f6"]
        lines += [
            f"### `{key}`",
            "",
            f"- 候选 {b['n_total']}；可行 {b['n_feasible']}；不可行 {b['n_infeasible']}",
            f"- 全局最优：`{best['axis_id']}` DPR={best['l2_dpr']:.1f} {best.get('labels')}",
            "- 前 10：",
        ]
        for row in b["top10"]:
            lines.append(
                f"  {row['rank']}. `{row['axis_id']}` {row['l2_dpr']:.1f} {pct(row['gap_vs_best'])}"
            )
        lines += [
            f"- 社区自适应：名次={ce['rank']} DPR={ce['l2_dpr']} 差距 {pct(ce['gap_vs_best'])}",
            f"- F6（不计砂金）：匹配 {cf['n_matching']}；最优名次={cf['rank_of_best_matching_f6']} "
            f"差距 {pct(cf['gap_vs_best'])}；砂金={cf['best_aven_label']}",
            "",
        ]
    lines += [
        "## E1.5 主结论（攻略速度 × 自适应穷举最优）",
        "",
        "| 对照 | ZONE 开 | ZONE 关 |",
        "|---|---:|---:|",
        f"| 椒丘最优 DPR | {jq_on:.1f} | {jq_off:.1f} |",
        f"| 佩拉最优 DPR | {pe_on:.1f} | {pe_off:.1f} |",
        f"| **新 vs 老** | {pct(e15['enum_jq_vs_pela_on'])} | {pct(e15['enum_jq_vs_pela_off'])} |",
        "",
    ]
    lines += ["## 开大 × 花火战技增益（攻略速度，终命中）", ""]
    for team, block in coverage.items():
        lines.append(f"### {team}")
        for k, v in block.items():
            if not isinstance(v, dict):
                continue
            if not v.get("feasible"):
                lines.append(f"- `{k}` 不可行 {v.get('reason')}")
                continue
            lines.append(
                f"- `{k}` 终命中花火战技增益={v.get('ult_sparkle_skill')} n={v.get('n_ult')} → **{v.get('verdict')}**"
            )
        lines.append("")
    lines += [
        "## 出口",
        "",
        "F 收尾完成，并入本批开头。佩拉社区轴按自适应重新排名；严格序列不可行不再写成「不在最优附近」。",
        "",
    ]
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print("wrote", OUT_MD)


if __name__ == "__main__":
    main()
