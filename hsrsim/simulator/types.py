"""
Core type definitions for HSR combat simulator.

Maps directly to Pfau et al. (2024) seven-tuple (D, C, B={S,A,E,V}, R):
- D: DamageFormula      (computed by simulator.damage_zones, not a class but a function)
- C: list[Character]
- B: Build              (composition of Stats + Actions + Effects + Variables)
- S: Stats              (passive numerical attributes)
- A: Action             (executable skill / spell / move)
- E: Effect             (buff / debuff / passive trait, time-bounded or permanent)
- V: Variable           (counters: energy, skill points, custom resources like Acheron's Nihility stacks)
- R: Rotation           (sequence of actions, the "axis" in HSR community parlance)

This schema is JSON-serializable so it can be read from `data/hsr/characters/*.json`.
"""
from __future__ import annotations

from enum import Enum
from typing import Literal, Optional
from pydantic import BaseModel, Field


# ============================================================
# ENUMS
# ============================================================

class DamageType(str, Enum):
    """The 6 damage type pathways in HSR (as of v4.0)."""
    DIRECT = "direct"          # Direct damage (uses 9-10 standard zones)
    DOT = "dot"                # Damage over time (no crit, no original-mult, no weaken)
    BREAK = "break"            # Weakness break damage (uses break_effect, no crit/dmg_boost)
    SUPER_BREAK = "super_break"  # Super break (uses break_effect + super_break_dmg_boost)
    ELATION = "elation"        # v4.0 Elation pathway (3 new zones, can crit)
    TRUE = "true"              # v3.0 True damage (bypasses ALL zones)


class Element(str, Enum):
    """7 damage elements in HSR. Each triggers a different break effect."""
    PHYSICAL = "physical"      # Bleed
    FIRE = "fire"              # Burn (DoT)
    ICE = "ice"                # Freeze
    LIGHTNING = "lightning"    # Shock (DoT)
    WIND = "wind"              # Wind Shear (DoT)
    QUANTUM = "quantum"        # Entanglement
    IMAGINARY = "imaginary"    # Imprisonment


class Path(str, Enum):
    """Character paths (命途). Determines passive set bonuses and play patterns."""
    DESTRUCTION = "destruction"
    THE_HUNT = "the_hunt"
    ERUDITION = "erudition"
    HARMONY = "harmony"
    NIHILITY = "nihility"
    PRESERVATION = "preservation"
    ABUNDANCE = "abundance"
    REMEMBRANCE = "remembrance"   # v3.0
    ELATION = "elation"            # v4.0


class ActionType(str, Enum):
    """Action categories. Determines resource consumption and turn behavior."""
    BASIC_ATTACK = "basic_attack"   # +1 SP, no energy cost
    SKILL = "skill"                  # -1 SP, +20-30 energy
    ULTIMATE = "ultimate"            # consumes full energy bar, instant cast
    TALENT = "talent"                # passive, triggered by conditions
    FOLLOW_UP = "follow_up"          # follow-up attack, triggered conditionally
    MEMO_SKILL = "memo_skill"        # 忆灵技（记忆路径）
    MEMO_TALENT = "memo_talent"      # 忆灵天赋（弹射等）
    TECHNIQUE = "technique"          # out-of-combat, ignored in v0.1


# ============================================================
# S: STATS (Pfau's S)
# ============================================================

class Stats(BaseModel):
    """Base numerical attributes. Composed from character base + light cone + relics + buffs."""
    # Primary
    hp_max: float = Field(..., description="Maximum HP")
    atk: float = Field(..., description="Attack stat (most damage scales off this)")
    defense: float = Field(..., description="Defense stat (defense zone)")
    speed: float = Field(default=100.0, description="Determines turn order via AV = 10000/Speed")

    # Combat ratios (already in [0,1], not %)
    crit_rate: float = Field(default=0.05, ge=0.0, le=1.0)
    crit_dmg: float = Field(default=0.50, ge=0.0)
    break_effect: float = Field(default=0.0, ge=0.0)         # 击破特攻
    ehr: float = Field(default=0.0, ge=0.0, description="Effect Hit Rate")
    effect_res: float = Field(
        default=0.0,
        ge=0.0,
        description="Effect RES (StatusResistanceBase)",
    )

    # Damage modifiers (additive within zone)
    dmg_boost: dict[Element | Literal["all"], float] = Field(default_factory=dict)
    res_pen: dict[Element | Literal["all"], float] = Field(default_factory=dict)
    # Enemy elemental RES (0.2 typical; 0 when weak to that element). Allies unused.
    element_res: dict[str, float] = Field(
        default_factory=dict,
        description="Per-element resistance fraction, e.g. {'lightning': 0.0, 'fire': 0.2}",
    )

    # Resources
    energy_max: float = Field(default=100.0, ge=0.0,
                              description="0 means character bypasses energy system (Acheron, Castorice, ...)")
    err: float = Field(
        default=1.0,
        ge=0.0,
        description="Energy Regeneration Rate (1.0 = 100%). Multiplies all energy gains.",
    )

    # v4.0 Elation stats
    elation: float = Field(default=0.0, ge=0.0)
    punchline: float = Field(default=0.0, ge=0.0)
    merrymake: float = Field(default=0.0, ge=0.0)


# ============================================================
# E: EFFECTS (Pfau's E) — buffs, debuffs, passives
# ============================================================

class EffectModifier(BaseModel):
    """A single stat modification. Effects are bags of these."""
    target_stat: str          # e.g. "crit_rate", "dmg_boost.fire", "def_reduction"
    operation: Literal["add", "mul", "set"] = "add"
    value: float
    # Empty = applies to all damaging actions. Use action type values:
    # skill / ultimate / basic_attack / follow_up / talent / memo_skill / ...
    applies_to_actions: list[str] = Field(
        default_factory=list,
        description="Action types this modifier applies to; empty means all",
    )


class EffectTarget(str, Enum):
    """Where an effect is applied at runtime."""
    SELF = "self"
    SINGLE_ALLY = "single_ally"
    ALL_ALLIES = "all_allies"
    SINGLE_ENEMY = "single_enemy"
    ALL_ENEMIES = "all_enemies"


class Effect(BaseModel):
    """A buff / debuff / passive trait. Lives on a character or enemy."""
    id: str
    name: str
    name_en: str | None = Field(
        default=None,
        description="English display name for UI / paper screenshots",
    )
    is_buff: bool = True       # True = buff on self/ally, False = debuff on enemy
    target: EffectTarget = EffectTarget.SELF
    duration_turns: int = -1   # -1 = permanent
    max_stacks: int = 1
    current_stacks: int = 1

    # What this effect changes
    modifiers: list[EffectModifier] = Field(default_factory=list)

    # Conditions (e.g. "only applies when target has bleed")
    conditions: list[str] = Field(default_factory=list)

    # Debuff apply chance (None = always apply; used with Stats.ehr vs target effect_res)
    base_chance: float | None = Field(
        default=None,
        ge=0.0,
        description="Base chance to apply this debuff; None = guaranteed",
    )

    # Duration tick phase on the holder's normal turn (C1).
    tick_timing: Literal["turn_start", "turn_end"] = Field(
        default="turn_end",
        description="When the holder's normal turn decrements this duration",
    )
    #optional DoT hit payload. When set + tick_timing=turn_start, turn_start
    # settles damage via DamageType.DOT zone matrix before decrementing duration.
    dot_instance: DamageInstance | None = Field(
        default=None,
        description="DoT hit (MV/element); None = duration-only at turn_start (no invent)",
    )
    dot_source_id: str | None = Field(
        default=None,
        description="Attacker id for DoT ATK scaling; None = holder (self-DoT)",
    )
    # Who applied this live instance (set by CharacterState.apply_effect).
    source_id: str | None = Field(
        default=None,
        description="Applier character id for this instance; used by LC wearer-DoT checks",
    )
    # Stack vuln that is not stacks×modifier (e.g. Jiaoqiu Ashen Roast: 15% + 5%/extra).
    vuln_at_one_stack: float | None = Field(
        default=None,
        description="If set with vuln_per_extra_stack, defender vuln = at_one + (stacks-1)*per_extra",
    )
    vuln_per_extra_stack: float | None = Field(
        default=None,
        description="Per stack beyond the first; used with vuln_at_one_stack",
    )
    # Set at apply time when the holder is mid action-phase of their own normal turn.
    skip_next_end_tick: bool = Field(
        default=False,
        description="If True, next turn_end tick clears the flag instead of decrementing",
    )
    # Reserved A/B judge class (not implemented in C1).
    buff_judge_class: str | None = Field(
        default=None,
        description="Reserved BUFF_JUDGE_CLASS slot; unused in C1",
    )

    # AV manipulation (Team-axis MVP): pct of target's full-turn AV to subtract
    action_advance_pct: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Action advance: reduce recipient AV by this fraction of one turn",
    )
    action_advance_skip_self: bool = Field(
        default=False,
        description="If True, skip advance when the skill caster targets themselves",
    )


# ============================================================
# V: VARIABLES (Pfau's V) — counters, custom resources
# ============================================================

class Variable(BaseModel):
    """A counter / custom resource. e.g. Acheron's Nihility stacks (0-9)."""
    id: str
    name: str
    name_en: str | None = Field(
        default=None,
        description="English display name for UI / paper screenshots",
    )
    value: float = 0
    min_value: float = 0
    max_value: float = float("inf")


# ============================================================
# A: ACTIONS (Pfau's A) — executable skills
# ============================================================

class DamageInstance(BaseModel):
    """A single damage event triggered by an action.
    
    An action can have multiple damage instances (e.g. an AOE hits multiple enemies,
    or an ultimate has 3 separate hits with different multipliers).
    """
    multiplier: float                              # MV% (technique multiplier as decimal, e.g. 2.4 = 240%)
    element: Element
    damage_type: DamageType = DamageType.DIRECT
    target: Literal["single", "blast", "aoe"] = "single"
    toughness_dmg: float = 30.0                    # 削韧值
    # StarRailDamageCal Role.calculate_damage is_hp: 0=ATK, 1=HP, 2=DEF
    scaling_stat: Literal["atk", "defense", "hp", "heal_accumulated"] = "atk"
    # Evernight-style memoria-dependent HP MV (Fribbels Evernight.ts)
    memoria_base: float | None = None
    memoria_per_four: float | None = None
    memoria_enhanced_threshold: int = 16
    memoria_enhanced_per_stack: float | None = None
    # E1.5 Pela E6: extra hit only when defender currently has a debuff.
    requires_defender_debuff: bool = False


class Action(BaseModel):
    """A skill / spell / executable move."""
    id: str
    name: str
    name_en: str | None = Field(
        default=None,
        description="English display name for UI / paper screenshots",
    )
    type: ActionType
    description: str = ""

    # Damage
    damage_instances: list[DamageInstance] = Field(default_factory=list)

    # Resource costs/gains
    energy_cost: float = 0.0           # negative = generates energy (× Stats.err on gain)
    sp_cost: int = 0                   # negative = generates SP (basic attacks: -1)

    # Datamine skill id → AvatarSkillConfig.SPBase . Hoisted from skill_level on load.
    skill_id: int | None = None

    #temporary SP pool ceiling for this action's gain (e.g. ult overflow to 10).
    sp_pool_temp_max: int | None = Field(
        default=None,
        description="If set, SP gains from this action use this cap instead of sp_team_max",
    )

    # Effects this action applies
    applies_effects: list[str] = Field(default_factory=list,
                                       description="effect IDs to apply (resolved at runtime)")
    effect_target: Literal["self", "enemy", "all_allies", "single_ally"] = Field(
        default="self",
        description="Where applies_effects land: self / enemy / all allies / single ally",
    )

    # Variable changes (e.g. "Nihility stack +1")
    variable_changes: dict[str, float] = Field(default_factory=dict)

    # Heal tally for remembrance (风堇): adds hp_max * [0] + [1] to heal_accumulated
    heal_tally_add: tuple[float, float] | None = None

    # Conditions to be available
    requires: list[str] = Field(default_factory=list)


# ============================================================
# B: BUILD (Pfau's B) — composition of S+A+E+V
# ============================================================

class Build(BaseModel):
    """A character's full build configuration."""
    stats: Stats
    actions: list[Action]
    effects: list[Effect] = Field(default_factory=list,
                                  description="passive traits (always-on effects)")
    variables: list[Variable] = Field(default_factory=list,
                                     description="custom resources/counters")


# ============================================================
# C: CHARACTERS (Pfau's C)
# ============================================================

class CombatProfileData(BaseModel):
    """Optional explicit combat profile (overrides pure inference when set)."""
    role: Literal["dps", "support", "hybrid"] = "dps"
    damage_axes: list[str] = Field(default_factory=list)
    scaling: list[str] = Field(default_factory=list)
    support_axes: list[str] = Field(default_factory=list)
    summary_zh: str = ""


class Character(BaseModel):
    """A combat actor (player or enemy)."""
    id: str
    name: str
    name_en: str | None = Field(
        default=None,
        description="Official / common English display name",
    )
    level: int = 80
    path: Path
    elements: list[Element] = Field(default_factory=list)
    weaknesses: list[Element] = Field(default_factory=list,
                                     description="for enemies: elements they're weak to")
    toughness_max: float = 100.0
    build: Build
    # Structural synergy metadata (not used by the combat sim)
    synergy_tags: list[str] = Field(
        default_factory=list,
        description="e.g. trailblaze_companion, skill_echo",
    )
    companion_roster: list[str] = Field(
        default_factory=list,
        description="Character ids that count as designated companions (e.g. 开拓同行)",
    )
    companion_on_assist: list[str] = Field(
        default_factory=list,
        description="Self effect ids unlocked when a companion_roster ally is present",
    )
    combat_profile: CombatProfileData | None = Field(
        default=None,
        description="Optional explicit damage/support axes; inferred when omitted",
    )
    # C9: immortal enemies take full damage for stats but HP floors at 1; no kill.
    immortal: bool = Field(
        default=False,
        description="If True, HP never drops below 1 and enemy_killed is not emitted",
    )
    #talent SP max bonus (from datamine ParamList #3[i]); stamped on load.
    talent_skill_id: int | None = Field(default=None, description="Talent AvatarSkillConfig id")
    talent_skill_level: int | None = Field(default=None, description="Talent skill level for ParamList")
    sp_team_max_bonus: int = Field(
        default=0,
        description="Added to team SP max while this ally is present (talent #3[i])",
    )
    #runtime config stamped from team / loadout (fail-loud if unwired).
    eidolon: int = Field(
        default=0,
        ge=0,
        le=6,
        description="Declared eidolon rank 0–6; wiring gate enforces wired max",
    )
    light_cone_id: int | None = Field(
        default=None,
        description="Equipped light cone id from loadout yaml, if any",
    )
    light_cone_superimposition: int = Field(
        default=1,
        ge=1,
        le=5,
        description="Light cone superimposition 1–5",
    )


# ============================================================
# R: ROTATION (Pfau's R) — the AXIS in HSR community parlance
# ============================================================

class RotationStep(BaseModel):
    """A single step in a rotation."""
    actor_id: str
    action_id: str
    target_id: Optional[str] = None
    timestamp: Optional[float] = None    # AV-based time, optional (engine schedules if None)


class Rotation(BaseModel):
    """A sequence of actions. The 'axis' (轴) we want to optimize."""
    steps: list[RotationStep]


# ============================================================
# SCENARIO: top-level input bundle (corresponds to Pfau's full input)
# ============================================================

class Scenario(BaseModel):
    """Top-level simulator input. JSON-serializable."""
    name: str
    description: str = ""

    # Pfau's C
    allies: list[Character]
    enemies: list[Character]

    # Pfau's R (optional: if not given, engine uses bot to generate)
    rotation: Optional[Rotation] = None

    # Termination
    max_rounds: int = 10  # retained for bookkeeping / legacy callers; not used to stop
    max_cycles: int = 20  # C9: stop when av_clock >= moc_cycle_av_cap(max_cycles)
    damage_target: Optional[float] = None   # stop early if total damage exceeds this

    #toughness cycle. realistic = break → recover on next normal turn;
    # always_unbroken = never break (f_broken always 0.9).
    toughness_mode: Literal["realistic", "always_unbroken"] = "realistic"


# Forward ref: Effect.dot_instance → DamageInstance (defined later in module).
Effect.model_rebuild()
