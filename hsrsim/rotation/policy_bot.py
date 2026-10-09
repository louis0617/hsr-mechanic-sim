"""L2 bot that follows a CycleAxis (paper-max: no shield constraint)."""
from __future__ import annotations

from hsrsim.rotation.schema import CycleAxis
from hsrsim.simulator.engine import Bot
from hsrsim.simulator.state import BattleState, CharacterState
from hsrsim.simulator.types import Action, ActionType, Scenario
from hsrsim.simulator.ult_timing import UltTimingStrategy
from hsrsim.simulator.bots import UltTimingBot


class AxisSPInfeasible(RuntimeError):
    """Axis asked for skill with no legal skill (typically SP=0). Do not fallback."""


class CycleAxisBot(Bot):
    """Normal turns: walk ``normal_cycle``. If ``strict_sp``, missing skill aborts."""

    def __init__(self, policy_actor: str, axis: CycleAxis, *, strict_sp: bool = True):
        self.policy_actor = policy_actor
        self.axis = axis
        self.strict_sp = strict_sp
        self._i = 0

    def select_action(
        self, actor: CharacterState, state: BattleState, legal_actions: list[Action]
    ) -> tuple[Action | None, str | None]:
        mem = self.axis.members.get(actor.char.id)
        if not legal_actions:
            return None, None
        ult = _pick_kind(legal_actions, "ult")
        only_ults = all(a.type == ActionType.ULTIMATE for a in legal_actions)
        picked = None
        if ult is not None and (
            only_ults
            or any(a.type in (ActionType.SKILL, ActionType.BASIC_ATTACK) for a in legal_actions)
        ):
            picked = ult
        elif mem is None:
            picked = legal_actions[0]
        elif mem.mode == "adaptive":
            want_skill = _adaptive_want_skill(actor, state, self.axis, mem)
            picked = (
                _pick_kind(legal_actions, "skill")
                if want_skill
                else _pick_kind(legal_actions, "basic")
            ) or _pick_kind(legal_actions, "basic") or legal_actions[0]
        else:
            cycle = [k for k in mem.normal_cycle if k in ("basic", "skill")] or ["basic"]
            want = cycle[self._i % len(cycle)]
            self._i += 1
            picked = _pick_kind(legal_actions, want)
            if picked is None:
                if self.strict_sp and want == "skill":
                    raise AxisSPInfeasible(
                        f"{actor.char.id} skill not legal (SP={state.sp_team_pool})"
                    )
                picked = _pick_kind(legal_actions, "basic") or legal_actions[0]
        target = _target_for(actor, state, picked, mem)
        return picked, target


def _pick_kind(legal: list[Action], kind: str) -> Action | None:
    if kind == "skill":
        types = {ActionType.SKILL}
    elif kind == "basic":
        types = {ActionType.BASIC_ATTACK}
    elif kind == "ult":
        types = {ActionType.ULTIMATE}
    else:
        return None
    for a in legal:
        if a.type in types:
            return a
    return None


def _effect_target_value(action: Action) -> str:
    et = getattr(action, "effect_target", None)
    if et is None:
        return ""
    return et.value if hasattr(et, "value") else str(et)


def _target_for(actor: CharacterState, state: BattleState, action: Action, mem) -> str:
    if any(getattr(action, "damage_instances", None) or []):
        return CycleAxisBot._first_alive_opponent(actor, state)
    if mem is not None and mem.default_target and state.find_char(mem.default_target):
        return mem.default_target
    et = _effect_target_value(action)
    if actor.char.id == "sparkle" and state.find_char("acheron"):
        return "acheron"
    if et in ("all_allies", "ally", "self"):
        return actor.char.id
    return CycleAxisBot._first_alive_opponent(actor, state)


def _adaptive_want_skill(
    actor: CharacterState, state: BattleState, axis: CycleAxis, mem
) -> bool:
    """Skill if SP≥k after reserving 1 point per higher-priority skill-wanter."""
    k = int(getattr(mem, "sp_min", 1) or 1)
    if k >= 99:
        return False
    sp = float(state.sp_team_pool)
    if sp + 1e-9 < k:
        return False
    pri = list(axis.sp_priority or [])
    if actor.char.id not in pri:
        return True
    my = pri.index(actor.char.id)
    reserve = 0
    for hid in pri[:my]:
        hm = axis.members.get(hid)
        if hm is None:
            continue
        hk = int(getattr(hm, "sp_min", 1) or 1)
        if hk < 99:
            reserve += 1
    return sp - 1.0 + 1e-9 >= reserve


def build_axis_bots(
    scenario: Scenario, axis: CycleAxis, *, strict_sp: bool = True
) -> dict[str, Bot]:
    ult = axis.acheron_ult()
    bots: dict[str, Bot] = {}
    for ally in scenario.allies:
        inner = CycleAxisBot(ally.id, axis, strict_sp=strict_sp)
        if ally.id == "acheron":
            bots[ally.id] = UltTimingBot(inner, strategy=ult)
        else:
            bots[ally.id] = UltTimingBot(
                inner,
                strategy=UltTimingStrategy.IMMEDIATE_WHEN_FULL.value,
            )
    return bots
