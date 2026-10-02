"""Outer grid over buff-only action rates (B8).

The inner LP only sees direct damage, so zero-damage support skills are
dominated by basics. This module enumerates those rates, fixes them in
``solve_flow``, and keeps the coverage fixed-point on each grid point.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import product
from typing import Sequence

from hsrsim.analytic.coverage import (
    ActionAdvance,
    ActionBuff,
    EnemyStackVuln,
    FixedPointResult,
    SpeedBuff,
    iterate_state,
)
from hsrsim.analytic.flow_model import CounterResource, FlowCharacter
from hsrsim.simulator.damage_zones import DamageContext

_ACTION_DAMAGE = {
    "basic": "basic_damage",
    "skill": "skill_damage",
    "ult": "ult_damage",
}
# Skill (or other) damage at or below this fraction of basic counts as
# "far below basic" for buff-only detection.
_DAMAGE_RATIO = 0.05
_DEFAULT_GRID = 11
_MAX_COMBOS = 1000


@dataclass(frozen=True)
class BuffOnlyAction:
    """One action that applies a modeled buff but deals negligible direct damage."""

    character_id: str
    action: str
    turn_cap: float
    action_damage: float
    basic_damage: float


@dataclass
class GridPoint:
    fixed_rates: dict[str, dict[str, float]]
    damage: float | None
    result: FixedPointResult | None
    feasible: bool
    error: str = ""


@dataclass
class BuffEnumResult:
    targets: list[BuffOnlyAction]
    grid_size: int
    points: list[GridPoint]
    best: GridPoint | None
    # One-dimensional slice when a single target is enumerated (or when
    # ``curve_character`` is set): rate of that action vs team damage.
    curve: list[tuple[float, float | None]] = field(default_factory=list)
    defective_damage: float | None = None
    damage_lift: float | None = None


def _action_damage(ch: FlowCharacter, action: str) -> float:
    return float(getattr(ch, _ACTION_DAMAGE[action]))


def is_buff_only_damage(action_damage: float, basic_damage: float) -> bool:
    if action_damage <= 0.0:
        return True
    if basic_damage <= 0.0:
        return False
    return action_damage <= _DAMAGE_RATIO * basic_damage


def find_buff_only_actions(
    characters: Sequence[FlowCharacter],
    buffs: Sequence[ActionBuff] = (),
    speed_buffs: Sequence[SpeedBuff] = (),
) -> list[BuffOnlyAction]:
    """Actions that apply an ActionBuff/SpeedBuff and deal ~0 direct damage."""
    by_id = {c.id: c for c in characters}
    keys: dict[tuple[str, str], None] = {}
    for buff in buffs:
        keys[(buff.applier_id, buff.source_action)] = None
    for buff in speed_buffs:
        keys[(buff.applier_id, buff.source_action)] = None
    out: list[BuffOnlyAction] = []
    for cid, action in keys:
        ch = by_id.get(cid)
        if ch is None:
            raise KeyError(f"buff applier {cid} is not in the cast")
        dmg = _action_damage(ch, action)
        basic = float(ch.basic_damage)
        if not is_buff_only_damage(dmg, basic):
            continue
        out.append(
            BuffOnlyAction(
                character_id=cid,
                action=action,
                turn_cap=float(ch.speed) / 100.0,
                action_damage=dmg,
                basic_damage=basic,
            )
        )
    out.sort(key=lambda t: (t.character_id, t.action))
    return out


def _grid_values(turn_cap: float, grid_size: int) -> list[float]:
    if grid_size < 2:
        raise ValueError(f"grid_size must be >= 2, got {grid_size}")
    if turn_cap < 0.0:
        raise ValueError(f"turn_cap must be >= 0, got {turn_cap}")
    return [turn_cap * i / (grid_size - 1) for i in range(grid_size)]


def enumerate_buff_actions(
    characters: list[FlowCharacter],
    buffs: list[ActionBuff] | None = None,
    *,
    speed_buffs: list[SpeedBuff] | None = None,
    advances: list[ActionAdvance] | None = None,
    stack_vulns: list[EnemyStackVuln] | None = None,
    action_contexts: dict[str, dict[str, DamageContext]] | None = None,
    apply_advance: bool = False,
    sp_other: float = 0.0,
    counters: list[CounterResource] | None = None,
    apply_overflow: bool = True,
    max_iter: int = 50,
    tol: float = 1e-6,
    grid_size: int = _DEFAULT_GRID,
    max_combos: int = _MAX_COMBOS,
    targets: list[BuffOnlyAction] | None = None,
    curve_character: str | None = None,
    curve_action: str = "skill",
) -> BuffEnumResult:
    """Grid-search buff-only action rates; pick the highest team damage.

    Raises ``ValueError`` if the grid product exceeds ``max_combos``.
    Infeasible LP points are kept with ``feasible=False`` and ignored when
    picking the best.
    """
    buffs = list(buffs or [])
    speed_buffs = list(speed_buffs or [])
    found = find_buff_only_actions(characters, buffs, speed_buffs)
    targets = list(targets) if targets is not None else found
    n_combos = 1
    for t in targets:
        n_combos *= grid_size
    if n_combos > max_combos:
        names = ", ".join(f"{t.character_id}.{t.action}" for t in targets)
        raise ValueError(
            f"buff-action grid has {n_combos} combinations "
            f"(grid_size={grid_size}, targets={len(targets)}: {names}), "
            f"above the limit of {max_combos}"
        )

    axes = [_grid_values(t.turn_cap, grid_size) for t in targets]
    points: list[GridPoint] = []
    best: GridPoint | None = None

    if not targets:
        # Nothing to pin: one unconstrained solve (same as iterate_state).
        result = iterate_state(
            characters,
            buffs,
            speed_buffs=speed_buffs,
            advances=advances,
            stack_vulns=stack_vulns,
            action_contexts=action_contexts,
            apply_advance=apply_advance,
            sp_other=sp_other,
            counters=counters,
            apply_overflow=apply_overflow,
            max_iter=max_iter,
            tol=tol,
        )
        point = GridPoint(
            fixed_rates={},
            damage=float(result.solution.total_damage),
            result=result,
            feasible=True,
        )
        return BuffEnumResult(
            targets=[],
            grid_size=grid_size,
            points=[point],
            best=point,
            curve=[],
            defective_damage=point.damage,
            damage_lift=0.0,
        )

    for rates in product(*axes):
        fixed: dict[str, dict[str, float]] = {}
        for target, rate in zip(targets, rates):
            fixed.setdefault(target.character_id, {})[target.action] = float(rate)
        try:
            result = iterate_state(
                characters,
                buffs,
                speed_buffs=speed_buffs,
                advances=advances,
                stack_vulns=stack_vulns,
                action_contexts=action_contexts,
                fixed_rates=fixed,
                apply_advance=apply_advance,
                sp_other=sp_other,
                counters=counters,
                apply_overflow=apply_overflow,
                max_iter=max_iter,
                tol=tol,
            )
            damage = float(result.solution.total_damage)
            point = GridPoint(
                fixed_rates=fixed,
                damage=damage,
                result=result,
                feasible=True,
            )
        except (RuntimeError, ValueError) as exc:
            point = GridPoint(
                fixed_rates=fixed,
                damage=None,
                result=None,
                feasible=False,
                error=str(exc),
            )
        points.append(point)
        if point.feasible and point.damage is not None:
            if best is None or point.damage > (best.damage or float("-inf")):
                best = point

    # Defective corner: every buff-only action fixed at 0 (all basic on those slots).
    defective = next(
        (
            p
            for p in points
            if p.feasible
            and all(
                abs(p.fixed_rates.get(t.character_id, {}).get(t.action, -1.0)) < 1e-12
                for t in targets
            )
        ),
        None,
    )
    defective_damage = None if defective is None else defective.damage
    damage_lift = None
    if best is not None and best.damage is not None and defective_damage is not None:
        damage_lift = best.damage - defective_damage

    curve: list[tuple[float, float | None]] = []
    curve_cid = curve_character
    curve_act = curve_action
    if curve_cid is None and len(targets) == 1:
        curve_cid = targets[0].character_id
        curve_act = targets[0].action
    if curve_cid is not None:
        for point in points:
            rate = point.fixed_rates.get(curve_cid, {}).get(curve_act)
            if rate is None:
                continue
            # When multiple targets exist, keep points where other fixed rates
            # are at their defective (0) value so the curve isolates one axis.
            others_ok = True
            if len(targets) > 1:
                for t in targets:
                    if t.character_id == curve_cid and t.action == curve_act:
                        continue
                    other = point.fixed_rates.get(t.character_id, {}).get(t.action, None)
                    if other is None or abs(float(other)) > 1e-12:
                        others_ok = False
                        break
            if others_ok:
                curve.append((float(rate), point.damage))

    return BuffEnumResult(
        targets=targets,
        grid_size=grid_size,
        points=points,
        best=best,
        curve=curve,
        defective_damage=defective_damage,
        damage_lift=damage_lift,
    )
