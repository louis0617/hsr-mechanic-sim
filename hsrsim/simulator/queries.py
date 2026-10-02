"""Battlefield unit queries () — no per-character specials."""
from __future__ import annotations

from typing import Any, Literal

from hsrsim.simulator.combat_rules import KnotTieBreak
from hsrsim.simulator.state import BattleState, CharacterState

Side = Literal["allies", "enemies", "all"]


def _side_units(state: BattleState, side: Side) -> list[CharacterState]:
    if side == "allies":
        return list(state.allies)
    if side == "enemies":
        return list(state.enemies)
    return list(state.allies) + list(state.enemies)


def _matches(unit: CharacterState, filt: dict[str, Any] | None) -> bool:
    if not filt:
        return True
    if filt.get("is_alive", True) and not unit.is_alive:
        return False
    if "avatar_base_type" in filt:
        want = filt["avatar_base_type"]
        got = getattr(unit.char, "avatar_base_type", None)
        if got is None:
            path = unit.char.path
            got = path.value if hasattr(path, "value") else str(path)
            aliases = {
                "nihility": "Warlock",
                "harmony": "Priest",
                "preservation": "Knight",
                "destruction": "Warrior",
                "hunt": "Rogue",
                "erudition": "Mage",
                "abundance": "Shaman",
                "remembrance": "Memory",
            }
            got = aliases.get(str(got).lower(), got)
        if str(got) != str(want):
            return False
    if "path" in filt:
        path = unit.char.path
        path_s = path.value if hasattr(path, "value") else str(path)
        if path_s != filt["path"] and str(path) != str(filt["path"]):
            return False
    if "has_effect_id" in filt:
        eid = filt["has_effect_id"]
        if not any(e.id == eid for e in unit.active_effects):
            return False
    if filt.get("has_shield"):
        if not any(
            "shield" in (e.id or "").lower()
            or any(m.target_stat == "shield" for m in e.modifiers)
            for e in unit.active_effects
        ):
            return False
    if "effect_stacks" in filt:
        spec = filt["effect_stacks"]
        eid = spec["id"]
        vmin = float(spec.get("min", 1))
        if effect_stacks(unit, eid) < vmin:
            return False
    return True


def effect_stacks(unit: CharacterState, effect_id: str) -> int:
    for e in unit.active_effects:
        if e.id == effect_id:
            return int(e.current_stacks)
    return 0


def _resolve_exclude(
    exclude: str | list[str] | None,
    *,
    self_id: str | None,
) -> set[str]:
    out: set[str] = set()
    if exclude is None:
        return out
    items = exclude if isinstance(exclude, list) else [exclude]
    for item in items:
        if item == "self":
            if self_id:
                out.add(self_id)
        else:
            out.add(str(item))
    return out


def _break_ties(
    tied: list[CharacterState], tie_break: KnotTieBreak
) -> CharacterState:
    if tie_break == "stable_unit_id_desc":
        return max(tied, key=lambda u: u.char.id)
    if tie_break == "lowest_av_remaining":
        return min(tied, key=lambda u: (u.av_remaining, u.char.id))
    # stable_unit_id_asc / random_seeded → deterministic id asc
    return min(tied, key=lambda u: u.char.id)


def count_units(
    state: BattleState,
    side: Side,
    filt: dict[str, Any] | None = None,
    *,
    exclude: str | list[str] | None = None,
    self_id: str | None = None,
) -> int:
    skip = _resolve_exclude(exclude, self_id=self_id)
    n = 0
    for u in _side_units(state, side):
        if u.char.id in skip:
            continue
        if _matches(u, filt):
            n += 1
    return n


def select_unit(
    state: BattleState,
    side: Side,
    filt: dict[str, Any] | None = None,
    *,
    order_by: str,
    tie_break: KnotTieBreak = "stable_unit_id_asc",
    exclude: str | list[str] | None = None,
    self_id: str | None = None,
    among_ids: list[str] | None = None,
) -> CharacterState | None:
    """Pick one unit. ``order_by`` e.g. ``effect_stacks:crimson_knot:desc``."""
    skip = _resolve_exclude(exclude, self_id=self_id)
    candidates: list[CharacterState] = []
    for u in _side_units(state, side):
        if u.char.id in skip:
            continue
        if among_ids is not None and u.char.id not in among_ids:
            continue
        if not u.is_alive:
            continue
        if _matches(u, filt):
            candidates.append(u)
    if not candidates:
        return None

    parts = order_by.split(":")
    if parts[0] == "effect_stacks" and len(parts) >= 2:
        eid = parts[1]
        descending = (parts[2] if len(parts) > 2 else "desc").lower() != "asc"
        scored = [(effect_stacks(u, eid), u) for u in candidates]
        best = max(s for s, _ in scored) if descending else min(s for s, _ in scored)
        tied = [u for s, u in scored if s == best]
        return _break_ties(tied, tie_break)

    if order_by == "av_remaining_asc":
        best = min(u.av_remaining for u in candidates)
        tied = [u for u in candidates if u.av_remaining == best]
        return _break_ties(tied, tie_break)

    return _break_ties(candidates, tie_break)
