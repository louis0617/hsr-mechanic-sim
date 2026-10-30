"""Cycle axis: per-cycle action sequence + ult insert + SP mix.

Period length is implicit (AV of one loop of the combined patterns).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

NormalKind = Literal["basic", "skill"]
UltPolicy = Literal["immediate_when_full", "wait_sparkle_skill_buff", "none"]


@dataclass
class MemberPolicy:
    actor_id: str
    """Repeating normal-turn cycle, e.g. EAA = skill, basic, basic."""
    normal_cycle: list[str]
    ult: UltPolicy = "immediate_when_full"
    default_target: str | None = None
    """cycle = 固定序列；adaptive = SP≥k 放战技否则普攻。"""
    mode: str = "cycle"
    """Minimum team SP to spend a skill (adaptive). 99 = never skill."""
    sp_min: int = 1

    def skill_share(self) -> float:
        if self.mode == "adaptive":
            if int(self.sp_min) >= 99:
                return 0.0
            if int(self.sp_min) <= 1:
                return 1.0
            return 0.5
        kinds = [k for k in self.normal_cycle if k in ("basic", "skill")]
        if not kinds:
            return 0.0
        return sum(1 for k in kinds if k == "skill") / len(kinds)


@dataclass
class CycleAxis:
    axis_id: str
    team_id: str
    members: dict[str, MemberPolicy]
    sources: list[dict[str, str]] = field(default_factory=list)
    notes: str = ""
    """High-first SP spend order for adaptive members."""
    sp_priority: list[str] = field(default_factory=list)

    def skill_shares(self) -> dict[str, float]:
        return {cid: p.skill_share() for cid, p in self.members.items()}

    def acheron_ult(self) -> str:
        p = self.members.get("acheron")
        if p is None:
            return "immediate_when_full"
        return p.ult if p.ult != "none" else "immediate_when_full"

    def to_dict(self) -> dict[str, Any]:
        return {
            "axis_id": self.axis_id,
            "team_id": self.team_id,
            "notes": self.notes,
            "sources": list(self.sources),
            "members": {
                cid: {
                    "actor_id": p.actor_id,
                    "normal_cycle": list(p.normal_cycle),
                    "ult": p.ult,
                    "default_target": p.default_target,
                    "mode": p.mode,
                    "sp_min": p.sp_min,
                    "skill_share": p.skill_share(),
                }
                for cid, p in self.members.items()
            },
            "sp_priority": list(self.sp_priority),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> CycleAxis:
        members = {}
        for cid, m in (raw.get("members") or {}).items():
            members[cid] = MemberPolicy(
                actor_id=str(m.get("actor_id") or cid),
                normal_cycle=[str(x) for x in m["normal_cycle"]],
                ult=m.get("ult") or "immediate_when_full",
                default_target=m.get("default_target"),
                mode=str(m.get("mode") or "cycle"),
                sp_min=int(m.get("sp_min") or 1),
            )
        return cls(
            axis_id=str(raw["axis_id"]),
            team_id=str(raw["team_id"]),
            members=members,
            sources=list(raw.get("sources") or []),
            notes=str(raw.get("notes") or ""),
            sp_priority=[str(x) for x in (raw.get("sp_priority") or [])],
        )


F6_COMPARE_IDS = ("sparkle", "jiaoqiu", "pela", "acheron")


def pattern_label(cycle: list[str]) -> str:
    kinds = [k for k in cycle if k in ("basic", "skill")]
    if kinds == ["skill"]:
        return "全E"
    if kinds == ["skill", "basic"]:
        return "EA"
    if kinds == ["skill", "basic", "basic"]:
        return "EAA"
    if kinds == ["skill", "skill", "basic"]:
        return "EEA"
    if kinds == ["basic"]:
        return "全A"
    return "/".join(kinds)


def f6_key(axis: CycleAxis) -> tuple:
    """Community-comparable projection: Sparkle / JQ|Pela / Acheron + Acheron ult.

    Adaptive members compare (mode, sp_min), not the unused cycle list.
    Aventurine is out of scope (paper-max vs live sustain).
    """
    parts = []
    for cid in ("sparkle", "jiaoqiu", "pela", "acheron"):
        p = axis.members.get(cid)
        if p is None:
            continue
        ult = p.ult if cid == "acheron" else "—"
        if p.mode == "adaptive":
            body = ("adaptive", int(p.sp_min))
        else:
            body = tuple(p.normal_cycle)
        parts.append((cid, body, ult))
    return tuple(parts)


def pattern_distance(a: CycleAxis, b: CycleAxis) -> int:
    """Hamming-style: mismatched member cycle/ult. Used for F-e action-pattern compare."""
    keys = set(a.members) | set(b.members)
    d = 0
    for k in keys:
        pa, pb = a.members.get(k), b.members.get(k)
        if pa is None or pb is None:
            d += 2
            continue
        if pa.mode != pb.mode or int(pa.sp_min) != int(pb.sp_min):
            d += 1
        if tuple(pa.normal_cycle) != tuple(pb.normal_cycle):
            d += 1
        if pa.ult != pb.ult:
            d += 1
    if tuple(a.sp_priority) != tuple(b.sp_priority):
        d += 1
    return d
