"""Battlefield field zones (E1.2): generic, not character-special-cased.

Jiaoqiu ult zone is one instance; any source can open a FieldZone with
``ult_vuln`` / ``proc_*`` filled from skill ParamList.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FieldZone:
    """One active field effect on the battle.

    Tick ``remaining_turns`` on the **source** ally's normal ``turn_start``.
    ``ult_vuln`` is additive into the vulnerability zone when the hitting
    action is an ultimate (generic; no source-id branch in damage code).
    """

    id: str
    source_id: str
    remaining_turns: int
    # Enemy-action proc (optional; 0 / empty = no proc).
    proc_base_chance: float = 0.0
    proc_effect_id: str | None = None
    max_triggers: int = 0
    triggers_left: int = 0
    # Vulnerability added for ultimate hits while zone is up.
    ult_vuln: float = 0.0
    # enemy_id → round_number of last successful proc (per-enemy-per-round).
    per_enemy_proc_round: dict[str, int] = field(default_factory=dict)

    def is_active(self) -> bool:
        return self.remaining_turns > 0


# Canonical id for Jiaoqiu L10 ult zone (ParamList [1.0, 0.6, 0.15, 3, 6]).
JIAOQIU_ULT_ZONE_ID = "jiaoqiu_ult_zone"
JIAOQIU_ZONE_DURATION = 3
JIAOQIU_ZONE_PROC_CHANCE = 0.6
JIAOQIU_ZONE_ULT_VULN = 0.15
JIAOQIU_ZONE_MAX_TRIGGERS = 6


def collect_zone_ult_vuln(zones: list[FieldZone], action_kind: str) -> float:
    """Sum ult_vuln from active zones when the hit is an ultimate."""
    if action_kind != "ultimate":
        return 0.0
    total = 0.0
    for z in zones:
        if z.is_active() and z.ult_vuln:
            total += float(z.ult_vuln)
    return total
