"""Shared counter-gain specs for L1 adapter and L2 EventBus ()."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from hsrsim.analytic.flow_model import GainRule
from hsrsim.simulator.types import ActionType, Character

_REPO = Path(__file__).resolve().parents[2]
_DEFAULT_PATH = _REPO / "data" / "hsr" / "triggers" / "counter_gains.json"

_ACTION_KEY = {
    ActionType.BASIC_ATTACK: "basic",
    ActionType.SKILL: "skill",
    ActionType.ULTIMATE: "ult",
}

GainKind = Literal[
    "action_inherent",
    "applies_debuff_during_skill_cast",
    "turn_start",
]


@dataclass(frozen=True)
class CounterGainSpec:
    """One counter-gain rule shared by L1 and L2."""

    id: str
    resource: str
    holder_id: str
    increment: float
    per_action_cap: float
    kind: GainKind
    filter: dict[str, Any]
    l2_hook: str


def _load_raw(path: Path | None = None) -> dict[str, Any]:
    return json.loads((path or _DEFAULT_PATH).read_text(encoding="utf-8"))


def _json_bool(key: str, default: bool, path: Path | None = None) -> bool:
    raw = _load_raw(path)
    if key in raw:
        return bool(raw[key])
    flags = raw.get("flags") or {}
    if key in flags:
        return bool(flags[key])
    return default


def zone_proc_counts_for_dream(
    path: Path | None = None, *, override: bool | None = None
) -> bool:
    """L1/L2 shared flag: ult-zone Ashen Roast on enemy action → Slashed Dream.

    Default True (community convention).
    """
    if override is not None:
        return bool(override)
    return _json_bool("ZONE_PROC_COUNTS_FOR_DREAM", True, path)


def figment_counts_for_dream(
    path: Path | None = None, *, override: bool | None = None
) -> bool:
    """Figment never counts as an enemy infliction; always False unless overridden."""
    if override is not None:
        return bool(override)
    raw = _load_raw(path)
    val = raw.get("FIGMENT_COUNTS_FOR_DREAM")
    if isinstance(val, dict):
        # Archived object — working definition is non-counting.
        return False
    if isinstance(val, bool):
        return val
    return False


def load_counter_gain_specs(
    path: Path | None = None,
) -> list[CounterGainSpec]:
    raw = _load_raw(path)
    out: list[CounterGainSpec] = []
    for row in raw.get("gain_rules", []):
        out.append(
            CounterGainSpec(
                id=str(row["id"]),
                resource=str(row["resource"]),
                holder_id=str(row["holder_id"]),
                increment=float(row["increment"]),
                per_action_cap=float(row["per_action_cap"]),
                kind=row["kind"],  # type: ignore[arg-type]
                filter=dict(row.get("filter") or {}),
                l2_hook=str(row.get("l2_hook") or ""),
            )
        )
    return out


def specs_for_resource(
    resource: str,
    *,
    path: Path | None = None,
) -> list[CounterGainSpec]:
    return [s for s in load_counter_gain_specs(path) if s.resource == resource]


def _debuff_action_fires(allies: list[Character]) -> dict[str, dict[str, float]]:
    fires: dict[str, dict[str, float]] = {}
    for ally in allies:
        effects = {e.id: e for e in ally.build.effects}
        per: dict[str, float] = {}
        for action in ally.build.actions:
            key = _ACTION_KEY.get(action.type)
            if key is None:
                continue
            if any(
                not effects[eid].is_buff
                for eid in action.applies_effects
                if eid in effects
            ):
                per[key] = 1.0
        if per:
            fires[ally.id] = per
    return fires


def _figment_sp_fires(allies: list[Character]) -> dict[str, dict[str, float]]:
    """Deprecated : figment no longer feeds R1 fires."""
    _ = allies
    return {}


def _enrich_acheron_trigger_r1_fires(
    debuff_fires: dict[str, dict[str, float]],
    allies: list[Character],
) -> dict[str, dict[str, float]]:
    """LC 23024 mirage → R1. Crimson Knot is NOT an R1 source ().

    : 集真赤是天赋产物，不算天赋触发源。L2 ``_on_effect_applied``
    已排除 ``crimson_knot``；战技上的 ``acheron_r2`` 来自泡影，不是集真赤。
    L1 不得把 knot 写进 R1 fires。
    """
    out = {cid: dict(actions) for cid, actions in debuff_fires.items()}
    for ally in allies:
        if ally.id != "acheron":
            continue
        if int(getattr(ally, "light_cone_id", 0) or 0) != 23024:
            continue
        per = out.setdefault("acheron", {})
        # Mirage applies once per attack (basic / skill / ult windows).
        per.setdefault("basic", 1.0)
        per.setdefault("skill", 1.0)
    return out


def specs_to_l1_gain_rules(
    specs: list[CounterGainSpec],
    allies: list[Character],
    *,
    figment_counts: bool | None = None,
) -> list[GainRule]:
    """Compile shared specs into L1 ``GainRule`` rows (with fires maps).

    ``figment_counts`` ignored (archived); kept for call-site compatibility.
    """
    _ = figment_counts
    rules: list[GainRule] = []
    debuff_fires = _enrich_acheron_trigger_r1_fires(
        _debuff_action_fires(allies), allies
    )
    by_id = {a.id: a for a in allies}
    for spec in specs:
        if spec.kind == "action_inherent":
            actor = str(spec.filter.get("actor_id") or spec.holder_id)
            atype = str(spec.filter.get("action_type") or "skill")
            key = {
                "basic_attack": "basic",
                "basic": "basic",
                "skill": "skill",
                "ultimate": "ult",
                "ult": "ult",
            }.get(atype, atype)
            rules.append(
                GainRule(
                    predicate=spec.id,
                    increment=spec.increment,
                    per_action_cap=spec.per_action_cap,
                    fires={actor: {key: 1.0}},
                )
            )
        elif spec.kind == "applies_debuff_during_skill_cast":
            rules.append(
                GainRule(
                    predicate=spec.id,
                    increment=spec.increment,
                    per_action_cap=spec.per_action_cap,
                    fires=debuff_fires,
                )
            )
        elif spec.kind == "turn_start":
            actor = str(spec.filter.get("actor_id") or spec.holder_id)
            min_e = int(spec.filter.get("min_eidolon") or 0)
            holder = by_id.get(actor)
            if holder is not None and int(getattr(holder, "eidolon", 0) or 0) < min_e:
                continue
            # One grant per normal turn ≡ once on basic or skill (mutually exclusive).
            rules.append(
                GainRule(
                    predicate=spec.id,
                    increment=spec.increment,
                    per_action_cap=spec.per_action_cap,
                    fires={actor: {"basic": 1.0, "skill": 1.0}},
                )
            )
        else:
            raise ValueError(f"unknown counter gain kind: {spec.kind!r}")
    return rules


def l1_l2_rule_fingerprint(specs: list[CounterGainSpec]) -> list[dict[str, Any]]:
    """Stable structural view for equality tests (no fires expansion)."""
    return [
        {
            "id": s.id,
            "resource": s.resource,
            "holder_id": s.holder_id,
            "increment": s.increment,
            "per_action_cap": s.per_action_cap,
            "kind": s.kind,
            "filter": dict(s.filter),
            "l2_hook": s.l2_hook,
        }
        for s in sorted(specs, key=lambda x: x.id)
    ]
