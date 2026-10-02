"""Energy grant helpers (): SPBase lookup x Stats.err."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

from hsrsim.simulator.state import CharacterState

REPO_ROOT = Path(__file__).resolve().parents[2]
SPBASE_TABLE = REPO_ROOT / "data" / "hsr" / "tables" / "avatar_skill_spbase.json"

LogFn = Callable[[str, dict[str, Any]], None]

_SKILL_CAST_KINDS = frozenset({"basic_attack", "skill", "ultimate"})


@lru_cache(maxsize=1)
def _load_spbase_table() -> dict[str, float | None]:
    raw = json.loads(SPBASE_TABLE.read_text(encoding="utf-8"))
    entries = raw.get("spbase_by_skill_id") or {}
    out: dict[str, float | None] = {}
    for k, v in entries.items():
        out[str(k)] = None if v is None else float(v)
    return out


def spbase_for_skill_id(skill_id: int | None) -> float | None:
    """Return AvatarSkillConfig.SPBase for ``skill_id``, or None if ABSENT / unknown."""
    if skill_id is None:
        return None
    return _load_spbase_table().get(str(int(skill_id)))


def grant_energy(
    actor: CharacterState,
    raw_delta: float,
    *,
    source: str,
    log: LogFn | None = None,
) -> float:
    """Apply energy gain: ``delta = raw_delta * stats.err``, capped at energy_max.

    No-op when ``energy_max == 0`` (Acheron etc.) or ``raw_delta == 0``.
    ERR does not apply to energy *spend* — callers must not pass negative raw_delta.
    """
    if raw_delta <= 0:
        return 0.0
    emax = float(actor.char.build.stats.energy_max)
    if emax <= 0:
        return 0.0
    err = float(actor.char.build.stats.err)
    delta = raw_delta * err
    before = float(actor.energy_current)
    actor.energy_current = min(emax, before + delta)
    applied = float(actor.energy_current) - before
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


def grant_spbase_on_action(
    actor: CharacterState,
    *,
    action_kind: str,
    skill_id: int | None,
    log: LogFn | None = None,
) -> float:
    """: action_resolved skill-cast regen from SPBase table."""
    if action_kind not in _SKILL_CAST_KINDS:
        return 0.0
    raw = spbase_for_skill_id(skill_id)
    if raw is None:
        return 0.0
    return grant_energy(
        actor,
        float(raw),
        source=f"spbase:{skill_id}",
        log=log,
    )
