"""Combat rule knobs ( UNKNOWN configs) and stack-transfer helpers."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

KnotTieBreak = Literal[
    "stable_unit_id_asc",
    "stable_unit_id_desc",
    "lowest_av_remaining",
    "random_seeded",
]


@dataclass
class CombatRules:
    """Three UNKNOWN knobs — working defaults; final values TBD after  contrast."""

    knot_tie_break: KnotTieBreak = "stable_unit_id_asc"
    debuff_refresh_counts: bool = True
    followup_counts_as_action: bool = True
    max_trigger_depth: int = 8
    # None = read data/hsr/triggers/counter_gains.json (L1/L2 shared).
    # figment_counts_for_dream: archived (ignored by triggers); kept for API compat.
    figment_counts_for_dream: bool | None = None
    zone_proc_counts_for_dream: bool | None = None
    # Rainblade / Thunder Core / talent RES. Off = pre-h stub ult.
    acheron_ult_e11h: bool = True
    # E1.5 17173 variant: None = DUMMY_HITS_PER_ENEMY_ACTION (standard=1).
    hits_per_enemy_action: int | None = None
    # If set, every dummy ally-hit targets this ally id (17173: 符玄受击×2).
    # None = path BaseAggro weighted (standard dummy).
    ally_hit_force_id: str | None = None


@dataclass
class StackTransferRule:
    """On enemy death, move stacks of ``effect_id`` to another living enemy."""

    effect_id: str
    direction: Literal["max", "min"] = "max"
    # If set, rule is active only while this ally is alive on the field.
    requires_ally_id: str | None = None


@dataclass
class ActionTriggerContext:
    """Per-action accumulator for R1–R5 settlement."""

    actor_id: str
    action_id: str
    action_kind: str
    turn_kind: str = "NORMAL"
    target_id: str | None = None
    debuff_targets: list[str] = field(default_factory=list)
    talent_settled: bool = False  # R2: debuff-during-skill package
    skill_r3_settled: bool = False  # R3: acheron skill inherent package
    # LC 23024: once per target per attack window.
    lc_mirage_marked: set[str] = field(default_factory=set)
    # LC 23023: once per shield-provide action window.
    lc_23023_cd_granted: bool = False
    # E1.5 Pela talent: once per action window.
    pela_talent_energy_done: bool = False

    def note_debuff_target(self, unit_id: str) -> None:
        if unit_id not in self.debuff_targets:
            self.debuff_targets.append(unit_id)
