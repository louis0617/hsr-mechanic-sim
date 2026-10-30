"""Inject our CycleAxis adaptive policy into HSR_Nous decision interface."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from hsrsim.axis.schema import CycleAxis, MemberPolicy
from hsrsim.l2_nous.ids import AVATAR_ID

# Nous ScriptedPolicy ult_timing strings
ULT_AFTER_ACTION = "after_action"
ULT_BEFORE_ACTION = "before_action"
ULT_NEVER = "never"

# our slug → Nous actor_id
_SLUG_BY_AID: dict[str, str] = {v: k for k, v in AVATAR_ID.items()}


def _slug(actor_id: str) -> str:
    return _SLUG_BY_AID.get(str(actor_id), str(actor_id))


def _adaptive_want_skill_nous(
    *,
    slug: str,
    sp: float,
    axis: CycleAxis,
    mem: MemberPolicy,
) -> bool:
    k = int(getattr(mem, "sp_min", 1) or 1)
    if k >= 99:
        return False
    if sp + 1e-9 < k:
        return False
    pri = list(axis.sp_priority or [])
    if slug not in pri:
        return True
    my = pri.index(slug)
    reserve = 0
    for hid in pri[:my]:
        hm = axis.members.get(hid)
        if hm is None:
            continue
        hk = int(getattr(hm, "sp_min", 1) or 1)
        if hk < 99:
            reserve += 1
    return sp - 1.0 + 1e-9 >= reserve


@dataclass
class AxisNousDecision:
    """HSR_Nous-compatible decision object driven by our CycleAxis."""

    axis: CycleAxis
    ult_timing: str = ULT_AFTER_ACTION
    _cycle_i: dict[str, int] = field(default_factory=dict)

    def select_action(self, actor_state: Any, legal: list, engine=None):
        if not legal:
            raise RuntimeError("legal_action_set empty")
        aid = str(actor_state.actor.actor_id)
        slug = _slug(aid)
        mem = self.axis.members.get(slug)
        # Prefer non-ultimate on the normal turn; ultimates go through select_ultimate.
        non_ult = [a for a in legal if getattr(a, "action_type", "") != "ultimate"]
        pool = non_ult or list(legal)
        if mem is None:
            return pool[0]
        if mem.mode == "adaptive":
            sp = float(getattr(engine, "skill_points", 0) if engine is not None else 0)
            want_skill = _adaptive_want_skill_nous(
                slug=slug, sp=sp, axis=self.axis, mem=mem
            )
            want = "skill" if want_skill else "basic"
            hit = next((a for a in pool if a.action_type == want), None)
            return hit or next((a for a in pool if a.action_type == "basic"), pool[0])
        cycle = [k for k in mem.normal_cycle if k in ("basic", "skill")] or ["basic"]
        i = self._cycle_i.get(slug, 0)
        want = cycle[i % len(cycle)]
        self._cycle_i[slug] = i + 1
        hit = next((a for a in pool if a.action_type == want), None)
        return hit or next((a for a in pool if a.action_type == "basic"), pool[0])

    def select_target(self, actor_state: Any, action_type: str, candidates: list, engine=None):
        """Pick ally/enemy from candidates. Support singles follow axis default_target.

        Mirrors L2 ``policy_bot._target_for``: Sparkle skill → main C (acheron) when
        present. Engine passes ally pool only for ``ally_single`` / ``ally_aoe``.
        """
        slug = _slug(str(actor_state.actor.actor_id))
        mem = self.axis.members.get(slug)
        want_slugs: list[str] = []
        if mem is not None and mem.default_target:
            want_slugs.append(str(mem.default_target))
        # L2 hard rule: sparkle ally skills prefer acheron even if axis omitted target
        if slug == "sparkle" and "acheron" not in want_slugs:
            want_slugs.append("acheron")
        for want in want_slugs:
            want_aid = AVATAR_ID.get(want)
            if want_aid is None:
                continue
            for c in candidates or []:
                cid = getattr(c, "actor_id", None) or getattr(
                    getattr(c, "actor", None), "actor_id", None
                )
                if str(cid) == str(want_aid):
                    return c
        return None

    def select_ultimate(self, actor_state: Any, ready: list, engine=None):
        # immediate_when_full for everyone on locked axes
        return next((a for st, a in ready if st is actor_state), None)

    def wait_segment(self, actor_state: Any, action, seg_index: int, engine=None):
        return None
