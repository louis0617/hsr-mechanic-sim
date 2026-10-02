"""C5 — ultimate resource resolution (energy bar or named counter)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from hsrsim.simulator.types import Action, ActionType, Character

_REQUIREMENT_GE = re.compile(
    r"^(?P<var>[A-Za-z_][A-Za-z0-9_]*)\s*(?P<op>>=|>)\s*(?P<val>-?\d+(?:\.\d+)?)$"
)


class UltResourceError(ValueError):
    """Character data lacks an explicit ultimate resource condition."""


@dataclass(frozen=True)
class UltResource:
    """Resolved ultimate gate: ``{resource, cost, max}``."""

    kind: Literal["energy", "variable"]
    resource: str
    cost: float
    max: float


def resolve_ult_resource(char: Character, action: Action) -> UltResource:
    """Derive ultimate resource from character/action data.

    - ``energy_max > 0`` → energy bar (SPBase characters).
    - ``energy_max == 0`` → named counter via ``requires`` (+ spend in
      ``variable_changes``). Missing ``requires`` raises ``UltResourceError``.
    """
    if action.type != ActionType.ULTIMATE:
        raise UltResourceError(
            f"{char.id} action {action.id} is not an ultimate"
        )
    emax = float(char.build.stats.energy_max)
    if emax > 0:
        cost = float(action.energy_cost) if action.energy_cost > 0 else emax
        return UltResource(kind="energy", resource="energy", cost=cost, max=emax)

    if not action.requires:
        raise UltResourceError(
            f"{char.id} ultimate {action.id}: energy_max=0 and no requires "
            "(named ult resource required; refusing default-legal)"
        )

    var_id: str | None = None
    threshold: float | None = None
    for req in action.requires:
        m = _REQUIREMENT_GE.match(req.strip())
        if not m:
            continue
        var_id = m.group("var")
        threshold = float(m.group("val"))
        break
    if var_id is None or threshold is None:
        raise UltResourceError(
            f"{char.id} ultimate {action.id}: requires={action.requires!r} "
            "has no parseable named-resource gate (e.g. nihility_stacks>=9)"
        )

    spend = float(action.variable_changes.get(var_id, 0.0))
    cost = -spend if spend < 0 else threshold
    max_val = threshold
    for var in char.build.variables:
        if var.id == var_id and var.max_value < float("inf"):
            max_val = float(var.max_value)
            break
    return UltResource(kind="variable", resource=var_id, cost=cost, max=max_val)


def ult_resource_ready(actor_energy: float, actor_variables: dict, res: UltResource) -> bool:
    """Whether the ultimate resource is currently full enough to cast."""
    if res.kind == "energy":
        return float(actor_energy) + 1e-9 >= float(res.max)
    current = float(actor_variables.get(res.resource, 0.0))
    return current + 1e-9 >= float(res.cost)
