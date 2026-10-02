"""Action Value (AV) timeline helpers for Team-axis MVP.

HSR convention: one full turn costs AV_BASE / effective_speed action value units.
Speed buffs are modeled as additive percentages on base speed (see TEAM_AXIS_MVP_SCOPE).
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from hsrsim.simulator.effect_agg import aggregate_effects
from hsrsim.simulator.types import Character, Effect

AV_BASE = 10000.0


class AVActor(Protocol):
    char: Character
    active_effects: list[Effect]
    av_remaining: float


def effective_speed(char_state: AVActor) -> float:
    """base_speed × (1 + sum of speed modifiers from active effects)."""
    base = char_state.char.build.stats.speed
    agg = aggregate_effects(char_state.active_effects, side="attacker")
    speed_pct = agg.get("speed", 0.0)
    return max(base * (1.0 + speed_pct), 1e-6)


def av_for_turn(char_state: AVActor) -> float:
    """AV cost of one full turn for this character at current effective speed."""
    return AV_BASE / effective_speed(char_state)


def advance_forward(target: AVActor, pct: float) -> float:
    """Reduce target AV by pct × their full-turn AV. Returns amount subtracted."""
    if pct <= 0.0:
        return 0.0
    delta = pct * av_for_turn(target)
    before = target.av_remaining
    target.av_remaining = max(0.0, before - delta)
    return before - target.av_remaining


def rescale_av_remaining_for_speed_change(
    actor: AVActor, old_speed: float, new_speed: float
) -> None:
    """Scale remaining AV when effective_speed changes: remaining *= old / new."""
    if old_speed <= 0.0 or new_speed <= 0.0:
        return
    if abs(old_speed - new_speed) < 1e-12:
        return
    actor.av_remaining *= old_speed / new_speed


def apply_effect_mutation(actor: AVActor, mutate: Callable[[], None]) -> None:
    """Run an effect-list mutation and rescale AV if effective_speed changed.

    All live effect apply / remove / refresh / stack / expire paths that can
    change speed must go through this helper — do not rescale elsewhere.
    """
    old_speed = effective_speed(actor)
    mutate()
    new_speed = effective_speed(actor)
    rescale_av_remaining_for_speed_change(actor, old_speed, new_speed)


# ============================================================
# MoC cycle (C9): cycle 1 = 150 AV; each later cycle = +100 AV
# ============================================================

MOC_CYCLE1_AV = 150.0
MOC_CYCLE_AV = 100.0


def moc_cycle_from_av(av_clock: float) -> int:
    """MoC cycle index from global AV clock.

    At exact boundaries 150 / 250 / … the completed cycle number is returned
    (150 → 1, 250 → 2). ``av_clock <= 0`` → 0.
    """
    import math

    if av_clock <= 0.0:
        return 0
    if av_clock <= MOC_CYCLE1_AV:
        return 1
    return 1 + int(math.ceil((av_clock - MOC_CYCLE1_AV) / MOC_CYCLE_AV - 1e-12))


def moc_cycle_av_cap(max_cycles: int) -> float:
    """AV clock at which a ``max_cycles`` fight ends (cycle N ends at this AV).

    Example: max_cycles=20 → 150 + 19×100 = 2050.
    """
    if max_cycles <= 0:
        return 0.0
    return MOC_CYCLE1_AV + (max_cycles - 1) * MOC_CYCLE_AV
