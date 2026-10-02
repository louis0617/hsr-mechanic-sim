"""
Damage zones (V_zone in the heterogeneous graph).

Design choice: one class per zone, all inheriting from `DamageZone`.
This lets the graph compiler (graph/compiler.py) enumerate all zones via reflection,
which is critical for RQ1: the V_zone node set is auto-derived from this file.

Adding a new zone (e.g. when HSR v5.0 introduces a new pathway) means:
1. Add a new class here inheriting from DamageZone
2. Register it in ZONE_REGISTRY
3. Update DAMAGE_PATHWAY_MATRIX to declare which damage types use it
The graph compiler picks it up automatically.

Reference: Fandom Wiki "Damage" + KQM SRL Damage Formula.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from hsrsim.simulator.types import DamageType, Element


@dataclass
class DamageContext:
    """Bundle of all info needed to compute any zone.
    
    Engine builds this once per damage instance and passes to every zone.
    Zones read what they need; ignoring fields is intentional (decouples zones).
    """
    # Attacker info
    attacker_level: int
    attacker_atk: float  # scaling stat value (ATK / DEF / HP max per DamageInstance.scaling_stat)
    attacker_crit_rate: float
    attacker_crit_dmg: float
    attacker_break_effect: float
    attacker_dmg_boost_pct: float       # additive sum of all dmg% sources for this hit
    attacker_res_pen_pct: float
    attacker_def_ignore_pct: float
    attacker_def_reduction_pct: float   # debuff applied to defender, not attacker stat
    
    # Defender info
    defender_level: int
    defender_def: float
    defender_res_pct: float
    defender_vuln_pct: float            # additive sum of all vuln% debuffs
    defender_mit_layers: list[float]    # multiplicative damage reduction layers
    defender_toughness_broken: bool

    # Action info
    skill_multiplier: float             # MV%
    extra_flat_damage: float = 0.0
    damage_type: DamageType = DamageType.DIRECT
    element: Element = Element.PHYSICAL

    # v4.0 Elation context
    elation_value: float = 0.0
    punchline_value: float = 0.0
    merrymake_value: float = 0.0

    # Optional zones filled by engine from active effects
    original_mult: float = 1.0
    weaken_pct: float = 0.0
    super_break_boost_pct: float = 0.0
    attacker_dot_boost_pct: float = 0.0  # DoT DMG% only (Eyes of the Prey etc.)
    #when set, BrokenZone uses 0.9 + 0.1 * uptime (L1 steady-state).
    defender_broken_uptime: float | None = None


class DamageZone(ABC):
    """Base class for a single multiplicative damage factor."""
    id: str = ""               # unique identifier (used as V_zone node ID in graph)
    name_zh: str = ""          # 中文名 (for paper figures)
    name_en: str = ""

    @abstractmethod
    def compute(self, ctx: DamageContext) -> float:
        """Return the multiplicative factor this zone contributes.
        
        Returns 1.0 to indicate "this zone is a no-op" (neutral).
        Engine multiplies all zone outputs together.
        """
        ...

    def applies_to(self, dmg_type: DamageType) -> bool:
        """Whether this zone participates in the given damage type's formula.
        
        Override per zone. Default: applies to all types.
        Used by both engine (skip non-applicable zones) and graph compiler
        (don't draw buff edges for non-applicable zones).
        """
        return True


# Break level multiplier table (KQM SRL / game data; linear interp between knots).
_BREAK_LEVEL_MULTIPLIER: dict[int, float] = {
    1: 7.507,
    20: 11.921,
    40: 14.415,
    60: 16.409,
    70: 17.056,
    80: 17.537,
    90: 18.019,
}


def break_level_multiplier(level: int) -> float:
    """Level scaling factor for Break / Super Break base damage."""
    level = max(1, min(90, level))
    if level in _BREAK_LEVEL_MULTIPLIER:
        return _BREAK_LEVEL_MULTIPLIER[level]
    keys = sorted(_BREAK_LEVEL_MULTIPLIER.keys())
    for i in range(len(keys) - 1):
        lo, hi = keys[i], keys[i + 1]
        if lo <= level <= hi:
            t = (level - lo) / (hi - lo)
            return _BREAK_LEVEL_MULTIPLIER[lo] * (1.0 - t) + _BREAK_LEVEL_MULTIPLIER[hi] * t
    return _BREAK_LEVEL_MULTIPLIER[keys[-1]]


# ============================================================
# THE 10+ STANDARD ZONES (HSR v4.0)
# ============================================================

class BaseZone(DamageZone):
    """f_base = (Stat × MV%) + ExtraFlat. The starting point of every damage formula."""
    id = "f_base"
    name_zh = "基础伤害区"
    name_en = "Base"

    def compute(self, ctx: DamageContext) -> float:
        # NOTE: For Break/SuperBreak, base damage uses level-scaling not ATK-scaling.
        # That special case is handled in BreakBaseZone below.
        # scaling stat (ATK/DEF/HP) is pre-resolved into ctx.attacker_atk by the engine.
        if ctx.damage_type in (DamageType.BREAK, DamageType.SUPER_BREAK):
            return 1.0  # those types use BreakBaseZone instead
        if ctx.damage_type == DamageType.TRUE:
            return ctx.extra_flat_damage  # true damage is fully flat
        return ctx.attacker_atk * ctx.skill_multiplier + ctx.extra_flat_damage


class OriginalMultZone(DamageZone):
    """f_origMult = independent multiplier from specific skills/eidolons.
    Examples: Acheron's Eidolon 6 grants a separate multiplier."""
    id = "f_origMult"
    name_zh = "原始倍率区"
    name_en = "OriginalMult"

    def compute(self, ctx: DamageContext) -> float:
        return ctx.original_mult

    def applies_to(self, dmg_type: DamageType) -> bool:
        return dmg_type in (DamageType.DIRECT, DamageType.ELATION)


class CritZone(DamageZone):
    """f_crit = CR×(1+CD) + (1-CR)×1. Expected-value smoothing for RL.
    
    Note: DOT and Break/SuperBreak do NOT crit (return 1.0).
    """
    id = "f_crit"
    name_zh = "双暴区"
    name_en = "Crit"

    def compute(self, ctx: DamageContext) -> float:
        cr = min(ctx.attacker_crit_rate, 1.0)  # hard cap at 100%
        cd = ctx.attacker_crit_dmg
        return cr * (1 + cd) + (1 - cr) * 1.0

    def applies_to(self, dmg_type: DamageType) -> bool:
        # CRITICAL FACT: DOT and Break do not crit. Elation does crit.
        return dmg_type in (DamageType.DIRECT, DamageType.ELATION)


class DmgBoostZone(DamageZone):
    """f_dmgBoost = 1 + Σ DMG%. ALL DMG% sources (element-specific, all-type, skill-type)
    sum additively here, then act as a single multiplicative factor.
    
    REMINDER: There is NO 'independent damage zone' in HSR. Everything additive.
    """
    id = "f_dmgBoost"
    name_zh = "增伤区"
    name_en = "DmgBoost"

    def compute(self, ctx: DamageContext) -> float:
        return 1.0 + ctx.attacker_dmg_boost_pct

    def applies_to(self, dmg_type: DamageType) -> bool:
        return dmg_type in (DamageType.DIRECT, DamageType.DOT)


class WeakenZone(DamageZone):
    """f_weaken = 1 - Weaken%. Currently no player-side application case in HSR."""
    id = "f_weaken"
    name_zh = "虚弱区"
    name_en = "Weaken"

    def compute(self, ctx: DamageContext) -> float:
        return max(0.0, 1.0 - ctx.weaken_pct)

    def applies_to(self, dmg_type: DamageType) -> bool:
        return dmg_type == DamageType.DIRECT


class DefZone(DamageZone):
    """f_def = (LvAtk + 20) / ((LvEnemy + 20) × max(0, 1 + DEF_bonus - DEF_red - DEF_ignore) + LvAtk + 20).
    
    KEY PROPERTY: convex in DEF reduction → marginal returns INCREASE as you stack defense shred.
    This is the central design phenomenon for the RL inner-loop reward shaping.
    
    Source: Fandom Wiki Damage page.
    """
    id = "f_def"
    name_zh = "防御区"
    name_en = "Def"

    def compute(self, ctx: DamageContext) -> float:
        if ctx.damage_type == DamageType.TRUE:
            return 1.0
        lf_atk = ctx.attacker_level + 20
        lf_def = ctx.defender_level + 20
        # Combined defense modifier (allows negative DEF_bonus from Lightning Lord etc)
        def_mod = max(0.0, 1.0 - ctx.attacker_def_reduction_pct - ctx.attacker_def_ignore_pct)
        return lf_atk / (lf_def * def_mod + lf_atk)


class ResZone(DamageZone):
    """f_res = 1 - (RES - PEN). Linear in penetration."""
    id = "f_res"
    name_zh = "抗性区"
    name_en = "Res"

    def compute(self, ctx: DamageContext) -> float:
        if ctx.damage_type == DamageType.TRUE:
            return 1.0
        effective_res = ctx.defender_res_pct - ctx.attacker_res_pen_pct
        # Capped at 0.9 in HSR (10% min damage taken)
        effective_res = max(-1.0, min(0.9, effective_res))
        return 1.0 - effective_res


class VulnZone(DamageZone):
    """f_vuln = 1 + Σ Vuln%. Additive within zone, multiplicative outside."""
    id = "f_vuln"
    name_zh = "易伤区"
    name_en = "Vuln"

    def compute(self, ctx: DamageContext) -> float:
        if ctx.damage_type == DamageType.TRUE:
            return 1.0
        return 1.0 + ctx.defender_vuln_pct


class MitZone(DamageZone):
    """f_mit = Π (1 - mit_i). MULTIPLICATIVE per layer, unlike most zones."""
    id = "f_mit"
    name_zh = "减伤区"
    name_en = "Mitigation"

    def compute(self, ctx: DamageContext) -> float:
        if ctx.damage_type == DamageType.TRUE:
            return 1.0
        result = 1.0
        for layer in ctx.defender_mit_layers:
            result *= (1.0 - layer)
        return max(0.1, result)  # capped at 90% mitigation in HSR


class BrokenZone(DamageZone):
    """f_broken = 0.9 if not broken else 1.0.

    When ``defender_broken_uptime`` is set (L1), use
    ``0.9 + 0.1 × clamp(uptime, 0, 1)`` as the steady-state blend.
    """
    id = "f_broken"
    name_zh = "韧性区"
    name_en = "Broken"

    def compute(self, ctx: DamageContext) -> float:
        if ctx.damage_type == DamageType.TRUE:
            return 1.0
        if ctx.defender_broken_uptime is not None:
            u = max(0.0, min(1.0, float(ctx.defender_broken_uptime)))
            return 0.9 + 0.1 * u
        return 1.0 if ctx.defender_toughness_broken else 0.9


# ============================================================
# BREAK / SUPER-BREAK ZONES
# ============================================================

class BreakBaseZone(DamageZone):
    """Replaces BaseZone for Break and SuperBreak damage.
    
    Formula uses level-scaling × toughness damage / 10, NOT ATK.
    This is the 'topology refactor' that decouples Break damage from character ATK.
    """
    id = "f_breakBase"
    name_zh = "击破基础区"
    name_en = "BreakBase"

    def compute(self, ctx: DamageContext) -> float:
        if ctx.damage_type not in (DamageType.BREAK, DamageType.SUPER_BREAK):
            return 1.0
        level_factor = break_level_multiplier(ctx.attacker_level)
        # skill_multiplier = toughness reduction on this hit (÷10 in formula)
        return level_factor * (ctx.skill_multiplier / 10.0)

    def applies_to(self, dmg_type: DamageType) -> bool:
        return dmg_type in (DamageType.BREAK, DamageType.SUPER_BREAK)


class BreakEffectZone(DamageZone):
    """f_be = 1 + BreakEffect%. The core scaling stat for break damage."""
    id = "f_be"
    name_zh = "击破特攻区"
    name_en = "BreakEffect"

    def compute(self, ctx: DamageContext) -> float:
        return 1.0 + ctx.attacker_break_effect

    def applies_to(self, dmg_type: DamageType) -> bool:
        return dmg_type in (DamageType.BREAK, DamageType.SUPER_BREAK)


class SuperBreakBoostZone(DamageZone):
    """f_sbBoost = 1 + super_break_dmg%. Only Firefly-style super break units use this."""
    id = "f_sbBoost"
    name_zh = "超击破伤害提高区"
    name_en = "SuperBreakBoost"

    def compute(self, ctx: DamageContext) -> float:
        return 1.0 + ctx.super_break_boost_pct

    def applies_to(self, dmg_type: DamageType) -> bool:
        return dmg_type == DamageType.SUPER_BREAK


class DotBoostZone(DamageZone):
    """f_dotBoost = 1 + DoT DMG%.

    Community: DoT DMG% (e.g. Eyes of the Prey) only applies to DoT hits and
    folds into the DMG Boost multiplier in-game; for graph reachability it is a
    DoT-only node analogous to f_sbBoost on Super Break.
    """
    id = "f_dotBoost"
    name_zh = "持续伤害增伤区"
    name_en = "DotBoost"

    def compute(self, ctx: DamageContext) -> float:
        return 1.0 + ctx.attacker_dot_boost_pct

    def applies_to(self, dmg_type: DamageType) -> bool:
        return dmg_type == DamageType.DOT


# ============================================================
# v4.0 ELATION ZONES (3 new zones added in v4.0)
# ============================================================

class ElationZone(DamageZone):
    """f_elation = 1 + Elation. Elation pathway's primary scaling stat."""
    id = "f_elation"
    name_zh = "欢愉乘区"
    name_en = "Elation"

    def compute(self, ctx: DamageContext) -> float:
        return 1.0 + ctx.elation_value

    def applies_to(self, dmg_type: DamageType) -> bool:
        return dmg_type == DamageType.ELATION


class PunchlineZone(DamageZone):
    """f_punchline = 1 + 5P/(P+240). Hyperbolic curve with built-in diminishing returns.
    
    This is the first HSR zone with a non-trivial (non-additive, non-linear) formula.
    Unique to v4.0 Elation.
    """
    id = "f_punchline"
    name_zh = "笑点乘区"
    name_en = "Punchline"

    def compute(self, ctx: DamageContext) -> float:
        p = ctx.punchline_value
        return 1.0 + (5.0 * p) / (p + 240.0)

    def applies_to(self, dmg_type: DamageType) -> bool:
        return dmg_type == DamageType.ELATION


class MerrymakeZone(DamageZone):
    """f_merrymake = 1 + Merrymake. Independent terminal multiplier (Yao Guang E6 etc)."""
    id = "f_merrymake"
    name_zh = "增笑度乘区"
    name_en = "Merrymake"

    def compute(self, ctx: DamageContext) -> float:
        return 1.0 + ctx.merrymake_value

    def applies_to(self, dmg_type: DamageType) -> bool:
        return dmg_type == DamageType.ELATION


# ============================================================
# REGISTRY: all zones in dependency order (multiplied left-to-right)
# ============================================================

ZONE_REGISTRY: list[DamageZone] = [
    BaseZone(),
    BreakBaseZone(),       # only for break/super_break
    OriginalMultZone(),
    CritZone(),
    DmgBoostZone(),
    DotBoostZone(),        # DoT-only DMG%
    WeakenZone(),
    ElationZone(),
    PunchlineZone(),
    MerrymakeZone(),
    BreakEffectZone(),
    SuperBreakBoostZone(),
    DefZone(),
    ResZone(),
    VulnZone(),
    MitZone(),
    BrokenZone(),
]


# ============================================================
# DAMAGE PATHWAY MATRIX (Table A1 in the paper, machine-readable)
# 
# Used for two purposes:
# 1. Engine: skip non-applicable zones during damage computation
# 2. RQ1 Graph compiler: don't draw buff edges from skill nodes to non-applicable zones
# ============================================================

DAMAGE_PATHWAY_MATRIX: dict[DamageType, list[str]] = {
    DamageType.DIRECT:      ["f_base", "f_origMult", "f_crit", "f_dmgBoost", "f_weaken",
                             "f_def", "f_res", "f_vuln", "f_mit", "f_broken"],
    # DoT: no crit / no original mult (HoYoLAB + Fandom). f_dotBoost = DoT DMG% only.
    DamageType.DOT:         ["f_base", "f_dotBoost", "f_dmgBoost",
                             "f_def", "f_res", "f_vuln", "f_mit", "f_broken"],
    DamageType.BREAK:       ["f_breakBase", "f_be",
                             "f_def", "f_res", "f_vuln", "f_mit", "f_broken"],
    DamageType.SUPER_BREAK: ["f_breakBase", "f_be", "f_sbBoost",
                             "f_def", "f_res", "f_vuln", "f_mit", "f_broken"],
    DamageType.ELATION:     ["f_base", "f_origMult", "f_crit",
                             "f_elation", "f_punchline", "f_merrymake",
                             "f_def", "f_res", "f_vuln", "f_mit", "f_broken"],
    DamageType.TRUE:        [],  # bypasses all zones
}


def compute_damage(ctx: DamageContext) -> tuple[float, dict[str, float]]:
    """Compute total damage as product of applicable zones.
    
    Returns:
        (total_damage, zone_breakdown) where zone_breakdown is {zone_id: factor_value}
        for logging/event-stream output (consumed by RQ2 reward synthesis).
    """
    zone_map = {z.id: z for z in ZONE_REGISTRY}
    applicable_ids = DAMAGE_PATHWAY_MATRIX[ctx.damage_type]
    
    if ctx.damage_type == DamageType.TRUE:
        return ctx.extra_flat_damage, {"f_true_flat": ctx.extra_flat_damage}
    
    breakdown: dict[str, float] = {}
    product = 1.0
    for zid in applicable_ids:
        zone = zone_map[zid]
        factor = zone.compute(ctx)
        breakdown[zid] = factor
        product *= factor
    
    return product, breakdown
