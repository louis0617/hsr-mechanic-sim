"""Energy grant helpers (C4): SPBase lookup x Stats.err."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

from hsrsim.simulator.state import CharacterState

REPO_ROOT = Path(__file__).resolve().parents[2]
SPBASE_TABLE = REPO_ROOT / "data" / "hsr" / "tables" / "avatar_skill_spbase.json"

LogFn = Callable[[str, dict[str, Any]], None]

_SKILL_CAST_KINDS = frozenset({"basic_attack", "skill", "ultimate", "follow_up"})


@lru_cache(maxsize=1)
def _load_spbase_table() -> dict[str, float | None]:
    raw = json.loads(SPBASE_TABLE.read_text(encoding="utf-8"))
    entries = raw.get("spbase_by_skill_id") or {}
    out: dict[str, float | None] = {}
    for k, v in entries.items():
        out[str(k)] = None if v is None else float(v)
    return out


def spbase_for_skill_id(skill_id: int | None) -> float | None:
    """Return AvatarSkillConfig.SPBase.

    ``None`` return means the table explicitly stores ABSENT (JSON null).
    Missing skill_id or missing table key raise — do not treat as 0.
    """
    if skill_id is None:
        raise KeyError("SPBase lookup: skill_id is None")
    table = _load_spbase_table()
    key = str(int(skill_id))
    if key not in table:
        raise KeyError(
            f"SPBase lookup: skill_id={skill_id} not in avatar_skill_spbase.json"
        )
    return table[key]


def grant_energy(
    actor: CharacterState,
    raw_delta: float,
    *,
    source: str,
    log: LogFn | None = None,
) -> float:
    """Apply energy gain: ``delta = raw_delta * stats.err``, capped at energy_max.

    Mortenax 1507101: excess fills overflow Energy up to 80. Hitting max dispels
    this unit's debuffs.
    """
    if raw_delta <= 0:
        return 0.0
    emax = float(actor.char.build.stats.energy_max)
    if emax <= 0:
        return 0.0
    err = float(actor.char.build.stats.err)
    delta = raw_delta * err
    before = float(actor.energy_current)
    leftover = (before + delta) - emax
    actor.energy_current = min(emax, before + delta)
    applied = float(actor.energy_current) - before
    if leftover > 1e-12 and getattr(actor.char, "id", None) == "mortenaxblade":
        from hsrsim.rules.mortenax import OVERFLOW_ENERGY_CAP, OVERFLOW_VAR

        cur_ov = float(actor.variables.get(OVERFLOW_VAR, 0.0))
        actor.variables[OVERFLOW_VAR] = min(OVERFLOW_ENERGY_CAP, cur_ov + leftover)
        if log:
            log(
                "overflow_energy",
                {
                    "actor_id": actor.char.id,
                    "leftover": leftover,
                    "overflow_after": actor.variables[OVERFLOW_VAR],
                    "source": source,
                },
            )
    if applied > 1e-12 and actor.energy_current + 1e-12 >= emax:
        _dispel_self_debuffs_on_energy_max(actor, log)
    if log and applied:
        log(
            "energy_gained",
            {
                "actor_id": actor.char.id,
                "raw_delta": raw_delta,
                "err": err,
                "delta": delta,
                "applied": applied,
                "energy_after": actor.energy_current,
                "source": source,
            },
        )
    return applied


def _dispel_self_debuffs_on_energy_max(actor: CharacterState, log: LogFn | None) -> None:
    if getattr(actor.char, "id", None) != "mortenaxblade":
        return
    removed = [e.id for e in list(actor.active_effects) if not e.is_buff]
    for eid in removed:
        actor.remove_effect(eid)
    if log and removed:
        log("debuffs_dispelled_energy_max", {"actor_id": actor.char.id, "removed": removed})


def convert_mortenax_overflow_after_ult(
    actor: CharacterState, *, log: LogFn | None = None
) -> float:
    """After Ultimate: clear overflow and add it to Energy (units already after ERR)."""
    from hsrsim.rules.mortenax import OVERFLOW_VAR

    if getattr(actor.char, "id", None) != "mortenaxblade":
        return 0.0
    ov = float(actor.variables.get(OVERFLOW_VAR, 0.0))
    actor.variables[OVERFLOW_VAR] = 0.0
    if ov <= 0:
        return 0.0
    emax = float(actor.char.build.stats.energy_max)
    before = float(actor.energy_current)
    actor.energy_current = min(emax, before + ov)
    applied = float(actor.energy_current) - before
    if actor.energy_current + 1e-12 >= emax:
        _dispel_self_debuffs_on_energy_max(actor, log)
    if log:
        log(
            "overflow_converted",
            {
                "actor_id": actor.char.id,
                "overflow": ov,
                "applied": applied,
                "energy_after": actor.energy_current,
            },
        )
    return applied


def grant_spbase_on_action(
    actor: CharacterState,
    *,
    action_kind: str,
    skill_id: int | None,
    log: LogFn | None = None,
) -> float:
    """C4: action_resolved skill-cast regen from SPBase table."""
    if action_kind not in _SKILL_CAST_KINDS:
        return 0.0
    if float(actor.char.build.stats.energy_max) <= 0:
        return 0.0
    if skill_id is None:
        cid = actor.char.id
        if cid == "lv95_dummy" or cid.startswith("lv95_dummy_"):
            return 0.0
        raise KeyError(f"{cid} {action_kind} missing skill_id for SPBase")
    raw = spbase_for_skill_id(skill_id)
    if raw is None:
        return 0.0
    return grant_energy(
        actor,
        float(raw),
        source=f"spbase:{skill_id}",
        log=log,
    )
