"""Action-advance interval and the L2 routing flag.

The lower solve forces every advance to 0. The upper solve feeds the
advance implied by the latest action rates back into the same fixed-point
loop as buff coverage. Speed buffs are coverage-weighted on both solves.
Extra turns, follow-ups, irregular SP or energy, and event triggers are
not linearized; they only set the routing flag.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from hsrsim.analytic.buff_enum import enumerate_buff_actions
from hsrsim.analytic.coverage import (
    ActionAdvance,
    ActionBuff,
    EnemyStackVuln,
    FixedPointResult,
    SpeedBuff,
    StackVulnState,
    iterate_state,
)
from hsrsim.analytic.flow_model import CounterResource, FlowCharacter, FlowSolution


@dataclass(frozen=True)
class ExtraTurn:
    """An extra turn that is not an action-advance ratio. Not solved in L1."""

    id: str
    actor_id: str
    note: str = ""


@dataclass(frozen=True)
class FollowUpTrigger:
    """A follow-up attack trigger. Not solved in L1."""

    id: str
    actor_id: str
    note: str = ""


@dataclass(frozen=True)
class IrregularResource:
    """SP or energy change that is not a constant per-action coefficient."""

    id: str
    kind: str
    note: str = ""

    def __post_init__(self) -> None:
        if self.kind not in ("sp", "energy"):
            raise ValueError(f"irregular resource kind must be sp or energy, got {self.kind!r}")


@dataclass(frozen=True)
class EventTrigger:
    """An event-trigger field. Not solved in L1."""

    id: str
    note: str = ""


@dataclass(frozen=True)
class FlowSpec:
    """Synthetic L1 input. Ordinary SP and energy coefficients are not flags."""

    characters: Sequence[FlowCharacter]
    buffs: Sequence[ActionBuff] = ()
    speed_buffs: Sequence[SpeedBuff] = ()
    advances: Sequence[ActionAdvance] = ()
    stack_vulns: Sequence[EnemyStackVuln] = ()
    action_contexts: Mapping[str, Mapping[str, object]] | None = None
    fixed_rates: Mapping[str, Mapping[str, float]] | None = None
    extra_turns: Sequence[ExtraTurn] = ()
    follow_ups: Sequence[FollowUpTrigger] = ()
    irregular_resources: Sequence[IrregularResource] = ()
    event_triggers: Sequence[EventTrigger] = ()
    counters: Sequence[CounterResource] | None = None
    sp_other: float = 0.0
    # B8: outer grid over buff-only actions. False keeps the defective LP-only path.
    enumerate_buff_actions: bool = True
    buff_grid_size: int = 11


@dataclass
class BoundSide:
    solution: FlowSolution
    coverages: dict[str, float]
    advances: dict[str, float]
    speeds: dict[str, float]
    iterations: int
    converged: bool
    stack_vulns: dict[str, StackVulnState] | None = None
    enum_groups: list[list[str]] | None = None
    combo_notes: list[str] | None = None
    fixed_rates: dict[str, dict[str, float]] | None = None
    buff_enum_lift: float | None = None


@dataclass
class IntervalResult:
    lower: BoundSide
    upper: BoundSide
    requires_l2: bool
    reasons: list[str]
    lower_curve: list[tuple[float, float | None]] | None = None
    upper_curve: list[tuple[float, float | None]] | None = None


def requires_l2(spec: FlowSpec) -> tuple[bool, list[str]]:
    """True when the spec contains a mechanic L1 does not pin to one number."""
    reasons: list[str] = []
    for adv in spec.advances:
        actions = ",".join(adv.source_actions)
        reasons.append(
            f"拉条 {adv.id}：{adv.applier_id} 的 {actions} 将 {adv.recipient_id} 前拉 {adv.ratio}"
        )
    for buff in spec.speed_buffs:
        reasons.append(
            f"速度修改 {buff.id}：{buff.applier_id} 的 {buff.source_action} "
            f"把 {buff.recipient_id} 的速度按覆盖率加权为 {buff.speed}"
        )
    for item in spec.extra_turns:
        reasons.append(f"额外回合 {item.id}：{item.actor_id}")
    for item in spec.follow_ups:
        reasons.append(f"追加攻击触发 {item.id}：{item.actor_id}")
    for item in spec.irregular_resources:
        label = "SP 非常规增减" if item.kind == "sp" else "能量非常规增减"
        reasons.append(f"{label} {item.id}：{item.note}" if item.note else f"{label} {item.id}")
    for item in spec.event_triggers:
        reasons.append(f"事件触发器 {item.id}" + (f"：{item.note}" if item.note else ""))
    return (bool(reasons), reasons)


def _side(
    state: FixedPointResult,
    *,
    fixed_rates: dict[str, dict[str, float]] | None = None,
    buff_enum_lift: float | None = None,
) -> BoundSide:
    return BoundSide(
        solution=state.solution,
        coverages=dict(state.coverages),
        advances=dict(state.advances),
        speeds=dict(state.speeds),
        iterations=state.iterations,
        converged=state.converged,
        stack_vulns=None if state.stack_vulns is None else dict(state.stack_vulns),
        enum_groups=None if state.enum_groups is None else [list(g) for g in state.enum_groups],
        combo_notes=None if state.combo_notes is None else list(state.combo_notes),
        fixed_rates=None if fixed_rates is None else {
            cid: dict(actions) for cid, actions in fixed_rates.items()
        },
        buff_enum_lift=buff_enum_lift,
    )


def _solve_side(
    characters: list[FlowCharacter],
    common: dict,
    *,
    apply_advance: bool,
    use_buff_enum: bool,
    grid_size: int,
    fixed_rates: Mapping[str, Mapping[str, float]] | None,
) -> tuple[BoundSide, list[tuple[float, float | None]] | None]:
    pinned = None if fixed_rates is None else {
        cid: dict(actions) for cid, actions in fixed_rates.items()
    }
    if use_buff_enum and pinned is None:
        enum = enumerate_buff_actions(
            characters,
            common.get("buffs") or [],
            speed_buffs=common.get("speed_buffs"),
            advances=common.get("advances"),
            stack_vulns=common.get("stack_vulns"),
            action_contexts=common.get("action_contexts"),
            apply_advance=apply_advance,
            sp_other=float(common.get("sp_other", 0.0)),
            counters=common.get("counters"),
            apply_overflow=bool(common.get("apply_overflow", True)),
            max_iter=int(common.get("max_iter", 50)),
            tol=float(common.get("tol", 1e-6)),
            grid_size=grid_size,
        )
        if enum.best is None or enum.best.result is None:
            raise RuntimeError("buff-action enumeration found no feasible grid point")
        return (
            _side(
                enum.best.result,
                fixed_rates=enum.best.fixed_rates,
                buff_enum_lift=enum.damage_lift,
            ),
            list(enum.curve),
        )
    state = iterate_state(
        characters,
        apply_advance=apply_advance,
        fixed_rates=pinned,
        **common,
    )
    return _side(state, fixed_rates=pinned), None


def solve_interval(
    spec: FlowSpec,
    *,
    max_iter: int = 50,
    tol: float = 1e-6,
) -> IntervalResult:
    """Lower bound ignores advances. Upper bound closes them in the shared loop.

    When ``spec.enumerate_buff_actions`` is true (default), each side runs the
    B8 outer grid over buff-only actions and keeps the highest-damage point.
    """
    flag, reasons = requires_l2(spec)
    characters = list(spec.characters)
    common = dict(
        buffs=list(spec.buffs),
        speed_buffs=list(spec.speed_buffs),
        advances=list(spec.advances),
        stack_vulns=list(spec.stack_vulns),
        action_contexts=None if spec.action_contexts is None else dict(spec.action_contexts),
        sp_other=float(spec.sp_other),
        counters=None if spec.counters is None else list(spec.counters),
        apply_overflow=True,
        max_iter=max_iter,
        tol=tol,
    )
    lower, lower_curve = _solve_side(
        characters,
        common,
        apply_advance=False,
        use_buff_enum=bool(spec.enumerate_buff_actions),
        grid_size=int(spec.buff_grid_size),
        fixed_rates=spec.fixed_rates,
    )
    upper, upper_curve = _solve_side(
        characters,
        common,
        apply_advance=True,
        use_buff_enum=bool(spec.enumerate_buff_actions),
        grid_size=int(spec.buff_grid_size),
        fixed_rates=spec.fixed_rates,
    )
    return IntervalResult(
        lower=lower,
        upper=upper,
        requires_l2=flag,
        reasons=reasons,
        lower_curve=lower_curve,
        upper_curve=upper_curve,
    )
