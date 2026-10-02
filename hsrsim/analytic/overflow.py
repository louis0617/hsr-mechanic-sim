"""Discrete pool overflow correction for L1 fill-then-spend resources ().

When a resource is acquired in random packets and spent only when the pool
reaches a hard cap, the crossing acquisition wastes the overshoot. Under
independent acquisitions the mean overshoot is ``E[X²] / (2 E[X])``, so the
LP should treat effective consume per spend as ``nominal + that excess``.

Applies to energy ultimates and named counters (e.g. Acheron dream).
Does **not** apply to team skill points (not fill-then-spend).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from hsrsim.analytic.flow_model import (
    CounterResource,
    FlowCharacter,
    _effective_gain,
    _uses_energy,
)

_ACTION_RATE = {"basic": "nb", "skill": "ns", "ult": "u"}


@dataclass(frozen=True)
class GainEvent:
    """One acquisition channel: event rate (per 100 AV) and packet size."""

    source: str
    rate: float
    amount: float


@dataclass
class ResourceOverflow:
    resource_id: str
    kind: str  # "energy" | "counter"
    nominal_consume: float
    average_excess: float
    effective_consume: float
    e_x: float
    e_x2: float
    events: list[GainEvent] = field(default_factory=list)


@dataclass
class OverflowReport:
    """Correction snapshot for one LP / fixed-point solution."""

    resources: dict[str, ResourceOverflow] = field(default_factory=dict)

    def energy_extra(self) -> dict[str, float]:
        return {
            rid.split(":", 1)[1]: r.average_excess
            for rid, r in self.resources.items()
            if r.kind == "energy"
        }

    def counter_extra(self) -> dict[str, float]:
        return {
            rid.split(":", 1)[1]: r.average_excess
            for rid, r in self.resources.items()
            if r.kind == "counter"
        }


def average_excess(events: list[tuple[float, float]] | list[GainEvent]) -> float:
    """``E[X²] / (2 E[X])`` for positive (rate, amount) acquisition events.

    Rates weight the mixture; zero-rate or non-positive amounts are ignored.
    Returns 0 when there is no positive mass.
    """
    pairs: list[tuple[float, float]] = []
    for ev in events:
        if isinstance(ev, GainEvent):
            rate, amount = float(ev.rate), float(ev.amount)
        else:
            rate, amount = float(ev[0]), float(ev[1])
        if rate > 0.0 and amount > 0.0:
            pairs.append((rate, amount))
    total_rate = sum(r for r, _ in pairs)
    if total_rate <= 0.0:
        return 0.0
    e_x = sum(r * a for r, a in pairs) / total_rate
    e_x2 = sum(r * a * a for r, a in pairs) / total_rate
    if e_x <= 0.0:
        return 0.0
    return e_x2 / (2.0 * e_x)


def _moments(events: list[GainEvent]) -> tuple[float, float]:
    pairs = [(e.rate, e.amount) for e in events if e.rate > 0.0 and e.amount > 0.0]
    total_rate = sum(r for r, _ in pairs)
    if total_rate <= 0.0:
        return 0.0, 0.0
    e_x = sum(r * a for r, a in pairs) / total_rate
    e_x2 = sum(r * a * a for r, a in pairs) / total_rate
    return e_x, e_x2


def energy_gain_events(
    ch: FlowCharacter,
    rates: dict[str, dict[str, float]],
) -> list[GainEvent]:
    """Energy packet sizes already include ERR (same as the LP gain row)."""
    if not _uses_energy(ch):
        return []
    row = rates.get(ch.id) or {}
    err = float(ch.err)
    out: list[GainEvent] = []
    for action, key, raw in (
        ("basic", "nb", ch.energy_basic),
        ("skill", "ns", ch.energy_skill),
        ("ult", "u", ch.energy_ult),
    ):
        amount = err * float(raw)
        rate = float(row.get(key, 0.0))
        if amount > 0.0 and rate > 0.0:
            out.append(GainEvent(source=f"{ch.id}.{action}", rate=rate, amount=amount))
    hit_amount = err * float(ch.energy_hit)
    hit_rate = float(ch.hits_per_100_av)
    if hit_amount > 0.0 and hit_rate > 0.0:
        out.append(GainEvent(source=f"{ch.id}.hit", rate=hit_rate, amount=hit_amount))
    return out


def counter_gain_events(
    counter: CounterResource,
    rates: dict[str, dict[str, float]],
) -> list[GainEvent]:
    out: list[GainEvent] = []
    for rule in counter.rules:
        for cid, actions in rule.fires.items():
            row = rates.get(cid) or {}
            for action, fires in actions.items():
                coef = _effective_gain(rule.increment, float(fires), rule.per_action_cap)
                if coef <= 0.0:
                    continue
                key = _ACTION_RATE.get(action)
                if key is None:
                    continue
                rate = float(row.get(key, 0.0))
                if rate <= 0.0:
                    continue
                out.append(
                    GainEvent(
                        source=f"{counter.name}:{rule.predicate}:{cid}.{action}",
                        rate=rate,
                        amount=coef,
                    )
                )
    return out


def build_overflow_report(
    characters: list[FlowCharacter],
    rates: dict[str, dict[str, float]],
    *,
    counters: list[CounterResource] | None = None,
) -> OverflowReport:
    """Compute average excess for every fill-then-spend resource from rates."""
    report = OverflowReport()
    for ch in characters:
        if not _uses_energy(ch):
            continue
        events = energy_gain_events(ch, rates)
        e_x, e_x2 = _moments(events)
        excess = average_excess(events)
        nominal = float(ch.energy_cost)
        rid = f"energy:{ch.id}"
        report.resources[rid] = ResourceOverflow(
            resource_id=rid,
            kind="energy",
            nominal_consume=nominal,
            average_excess=excess,
            effective_consume=nominal + excess,
            e_x=e_x,
            e_x2=e_x2,
            events=events,
        )
    for counter in counters or []:
        events = counter_gain_events(counter, rates)
        e_x, e_x2 = _moments(events)
        excess = average_excess(events)
        buffer = float(getattr(counter, "overflow_buffer", 0.0) or 0.0)
        if buffer > 0.0:
            # Soft buffer (四相断我 max B): hard waste only after buffer is full.
            # excess is mean overshoot past the hard cap; buffer absorbs up to B
            # units of that overshoot → hard waste = max(0, excess − B).
            # (: removed unfounded 0.5×buffer coefficient.)
            excess = max(0.0, excess - buffer)
        # Single consume action amount (Acheron: ult → 9)
        nominal = 0.0
        if counter.consume:
            nominal = float(next(iter(counter.consume.values())))
        rid = f"counter:{counter.name}"
        report.resources[rid] = ResourceOverflow(
            resource_id=rid,
            kind="counter",
            nominal_consume=nominal,
            average_excess=excess,
            effective_consume=nominal + excess,
            e_x=e_x,
            e_x2=e_x2,
            events=events,
        )
    return report
