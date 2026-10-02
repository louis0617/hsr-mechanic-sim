"""L1 steady-state flow model: LP core (B1) plus counter rows (B3).

Coverage, speed-buff, and action-advance fixed-point iteration share one
loop in ``coverage.py``. Interval output and ``requires_l2`` are in
``bounds.py``. No character IO, no simulator.
Energy and counter rows are the same balance inequality: consumption rate
minus gain rate is at most a constant (hit energy, or 0 for counters).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import linprog


@dataclass(frozen=True)
class FlowCharacter:
    """One ally's rates, per 100 AV. Damage is an input, not computed here."""

    id: str
    speed: float
    basic_damage: float
    skill_damage: float
    ult_damage: float = 0.0
    sp_add: float = 0.0
    sp_need: float = 0.0
    sp_ult: float = 0.0
    energy_basic: float = 0.0
    energy_skill: float = 0.0
    energy_ult: float = 0.0
    energy_hit: float = 0.0
    energy_cost: float = 0.0
    err: float = 1.0
    hits_per_100_av: float = 0.0


_ACTIONS = ("basic", "skill", "ult")


@dataclass(frozen=True)
class GainRule:
    """One way a counter is gained.

    ``fires`` is the caller's predicate marking: character id → action →
    how many times that action fires the predicate. ``per_action_cap``
    limits how many of those firings count.
    """

    predicate: str
    increment: float
    per_action_cap: float
    fires: dict[str, dict[str, float]]


@dataclass(frozen=True)
class CounterResource:
    """Steady-state counter. ``cap`` is the stack limit; the LP uses ``consume``."""

    name: str
    holder_id: str
    cap: float
    consume: dict[str, float]
    rules: tuple[GainRule, ...] | list[GainRule]
    #soft overshoot buffer (Acheron 四相断我 max 3) before hard waste.
    overflow_buffer: float = 0.0
    #gains outside LP actions (e.g. Aventurine FUA → R1 dream via 23023).
    external_gain: float = 0.0


@dataclass
class FlowSolution:
    """Optimal rates and shadow prices of the maximized damage.

    ``duals`` is keyed by constraint name. Each value is the partial
    derivative of maximized damage with respect to the right-hand side
    of that constraint in canonical form (``A x <= b`` or ``A x = b``).
    For ``sp_balance``, that is extra damage per +1 ``sp_other``.
    """

    rates: dict[str, dict[str, float]]
    total_damage: float
    duals: dict[str, float]
    success: bool
    message: str = ""
    resource_gains: dict[str, float] | None = None
    #fill-then-spend overflow correction used / implied by this solve.
    overflow: object | None = None


def _uses_energy(ch: FlowCharacter) -> bool:
    return any(
        abs(float(v)) > 0.0
        for v in (
            ch.energy_cost,
            ch.energy_basic,
            ch.energy_skill,
            ch.energy_ult,
            ch.energy_hit,
        )
    )


def _balance_row(
    n_vars: int,
    consume: dict[int, float],
    gain: dict[int, float],
    gain_const: float,
) -> tuple[np.ndarray, float]:
    """``consume·x - gain·x <= gain_const`` (gain rate covers consumption)."""
    row = np.zeros(n_vars)
    for idx, coef in consume.items():
        row[idx] += float(coef)
    for idx, coef in gain.items():
        row[idx] -= float(coef)
    return row, float(gain_const)


def _action_index(char_index: int, action: str) -> int:
    if action not in _ACTIONS:
        raise ValueError(f"action must be basic/skill/ult, got {action!r}")
    return 3 * char_index + _ACTIONS.index(action)


def _effective_gain(increment: float, fires: float, per_action_cap: float) -> float:
    if fires <= 0.0:
        return 0.0
    return float(increment) * min(float(fires), float(per_action_cap))


def sp_other_from_initial(initial_sp: float, av_clock: float) -> float:
    """Amortize starting team SP over fight length as a per-100-AV rate.

    ``sp_other = initial_sp / (av_clock / 100)``. Used as the LP ``sp_other``
    input (not a solver slack).
    """
    if av_clock <= 0.0:
        raise ValueError("av_clock must be > 0")
    return float(initial_sp) / (float(av_clock) / 100.0)


def solve_flow(
    characters: list[FlowCharacter],
    *,
    sp_other: float = 0.0,
    counters: list[CounterResource] | None = None,
    extra_turns: dict[str, float] | None = None,
    fixed_rates: dict[str, dict[str, float]] | None = None,
    energy_consume_extra: dict[str, float] | None = None,
    counter_consume_extra: dict[str, float] | None = None,
) -> FlowSolution:
    """Maximize sum(rate * damage) per 100 AV.

    Constraints
    - turns: ``nb + ns = speed / 100 + extra_turns``
    - sp_balance: ``sum(sp_add*nb - sp_need*ns + sp_ult*u) + sp_other >= 0``
    - energy / counter: consumption rate <= gain rate (shared row builder)

    ``extra_turns`` is the action-advance term ``adv_i``. Callers that omit
    it keep the B1 equality ``nb + ns = speed / 100``.

    ``fixed_rates`` pins named actions (e.g. ``{"S": {"skill": 0.5}}``) to
    exact values via variable bounds. Used by the outer buff-action grid so
    the LP cannot discard zero-damage support skills.

    ``energy_consume_extra`` / ``counter_consume_extra`` () add the
    fill-then-spend average overshoot to nominal consume per spend action.
    """
    if not characters:
        raise ValueError("solve_flow requires at least one character")
    ids = [c.id for c in characters]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate character ids: {ids}")
    index = {c.id: i for i, c in enumerate(characters)}
    counters = list(counters or [])
    names = [c.name for c in counters]
    if len(names) != len(set(names)):
        raise ValueError(f"duplicate counter names: {names}")
    energy_extra = {k: float(v) for k, v in (energy_consume_extra or {}).items()}
    counter_extra = {k: float(v) for k, v in (counter_consume_extra or {}).items()}

    by_id = {c.id: c for c in characters}
    for counter in counters:
        holder = by_id.get(counter.holder_id)
        if holder is None:
            raise KeyError(f"counter {counter.name} holder {counter.holder_id} is not in the cast")
        if counter.cap < 0:
            raise ValueError(f"counter {counter.name} cap must be >= 0")
        if _uses_energy(holder):
            raise ValueError(
                f"{holder.id} cannot be constrained by both energy and counter {counter.name}"
            )

    fixed_rates = fixed_rates or {}
    for cid, actions in fixed_rates.items():
        if cid not in index:
            raise KeyError(f"fixed_rates refers to unknown character {cid}")
        for action, value in actions.items():
            if action not in _ACTIONS:
                raise ValueError(f"fixed_rates action must be basic/skill/ult, got {action!r}")
            if float(value) < 0.0:
                raise ValueError(f"fixed_rates[{cid}][{action}] must be >= 0, got {value}")

    n = len(characters)
    n_vars = 3 * n
    c_obj = np.zeros(n_vars)
    a_eq = np.zeros((n, n_vars))
    b_eq = np.zeros(n)
    names_eq: list[str] = []
    ub_rows: list[np.ndarray] = []
    ub_rhs: list[float] = []
    names_ub: list[str] = []
    bounds: list[tuple[float, float | None]] = [(0.0, None)] * n_vars

    sp_row = np.zeros(n_vars)
    for i, ch in enumerate(characters):
        ib, isk, iu = _action_index(i, "basic"), _action_index(i, "skill"), _action_index(i, "ult")
        c_obj[ib] = -float(ch.basic_damage)
        c_obj[isk] = -float(ch.skill_damage)
        c_obj[iu] = -float(ch.ult_damage)

        a_eq[i, ib] = 1.0
        a_eq[i, isk] = 1.0
        b_eq[i] = float(ch.speed) / 100.0 + float((extra_turns or {}).get(ch.id, 0.0))
        names_eq.append(f"turns:{ch.id}")

        sp_row[ib] += -float(ch.sp_add)
        sp_row[isk] += float(ch.sp_need)
        sp_row[iu] += -float(ch.sp_ult)

        if _uses_energy(ch):
            err = float(ch.err)
            consume_u = float(ch.energy_cost) + float(energy_extra.get(ch.id, 0.0))
            row, rhs = _balance_row(
                n_vars,
                consume={iu: consume_u},
                gain={
                    ib: err * float(ch.energy_basic),
                    isk: err * float(ch.energy_skill),
                    iu: err * float(ch.energy_ult),
                },
                gain_const=err * float(ch.energy_hit) * float(ch.hits_per_100_av),
            )
            ub_rows.append(row)
            ub_rhs.append(rhs)
            names_ub.append(f"energy:{ch.id}")

    for cid, actions in fixed_rates.items():
        i = index[cid]
        turns = float(b_eq[i])
        for action, value in actions.items():
            rate = float(value)
            if action in ("basic", "skill") and rate > turns + 1e-9:
                raise ValueError(
                    f"fixed_rates[{cid}][{action}]={rate} exceeds turn budget {turns}"
                )
            idx = _action_index(i, action)
            bounds[idx] = (rate, rate)

    ub_rows.insert(0, sp_row)
    ub_rhs.insert(0, float(sp_other))
    names_ub.insert(0, "sp_balance")

    for counter in counters:
        consume: dict[int, float] = {}
        gain: dict[int, float] = {}
        holder_i = index[counter.holder_id]
        extra = float(counter_extra.get(counter.name, 0.0))
        for action, amount in counter.consume.items():
            idx = _action_index(holder_i, action)
            consume[idx] = consume.get(idx, 0.0) + float(amount) + extra
            # Extra attaches once per counter (not per consume key); zero after first.
            extra = 0.0
        for rule in counter.rules:
            for cid, actions in rule.fires.items():
                if cid not in index:
                    raise KeyError(f"counter {counter.name} references unknown character {cid}")
                for action, fires in actions.items():
                    coef = _effective_gain(rule.increment, float(fires), rule.per_action_cap)
                    if coef == 0.0:
                        continue
                    idx = _action_index(index[cid], action)
                    gain[idx] = gain.get(idx, 0.0) + coef
        row, rhs = _balance_row(n_vars, consume, gain, float(counter.external_gain))
        ub_rows.append(row)
        ub_rhs.append(rhs)
        names_ub.append(f"counter:{counter.name}")

    res = linprog(
        c_obj,
        A_ub=np.vstack(ub_rows),
        b_ub=np.asarray(ub_rhs, dtype=float),
        A_eq=a_eq,
        b_eq=b_eq,
        bounds=bounds,
        method="highs",
    )
    if not res.success:
        raise RuntimeError(f"flow LP failed: {res.message}")

    # linprog minimizes -damage. Shadow prices below are for the max problem.
    ineq_marg = np.asarray(res.ineqlin.marginals, dtype=float)
    eq_marg = np.asarray(res.eqlin.marginals, dtype=float)
    duals: dict[str, float] = {}
    for name, marg in zip(names_ub, ineq_marg):
        duals[name] = float(-marg)
    for name, marg in zip(names_eq, eq_marg):
        duals[name] = float(-marg)

    x = np.asarray(res.x, dtype=float)
    rates: dict[str, dict[str, float]] = {}
    total = 0.0
    for i, ch in enumerate(characters):
        nb = float(x[_action_index(i, "basic")])
        ns = float(x[_action_index(i, "skill")])
        u = float(x[_action_index(i, "ult")])
        rates[ch.id] = {"nb": nb, "ns": ns, "u": u}
        total += nb * ch.basic_damage + ns * ch.skill_damage + u * ch.ult_damage

    resource_gains = {
        f"counter:{counter.name}": _realized_gain(counter, rates) for counter in counters
    }

    return FlowSolution(
        rates=rates,
        total_damage=float(total),
        duals=duals,
        success=True,
        message=str(res.message),
        resource_gains=resource_gains,
    )


def _realized_gain(counter: CounterResource, rates: dict[str, dict[str, float]]) -> float:
    rate_key = {"basic": "nb", "skill": "ns", "ult": "u"}
    gained = float(counter.external_gain)
    for rule in counter.rules:
        for cid, actions in rule.fires.items():
            for action, fires in actions.items():
                coef = _effective_gain(rule.increment, float(fires), rule.per_action_cap)
                gained += coef * rates[cid][rate_key[action]]
    return gained

