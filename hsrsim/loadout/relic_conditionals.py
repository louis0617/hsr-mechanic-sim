"""E2 minimal set: paper-loadout relic (b)/(c) conditionals for L1 + L2.

Evidence: RelicSetSkillConfig AbilityParamList @ turnbasedgamedata
(D9_0_LC_RELIC_STRUCTURE.md). Chinese skill text from Res / community mirrors
with ParamList slots.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from hsrsim.analytic.coverage import ActionBuff
from hsrsim.simulator.types import Character

# RelicSetSkillConfig AbilityParamList (RequireNum) — verbatim numeric slots.
PIONEER_2PC_DMG = 0.12  # Set 117 RequireNum=2 AbilityParamList[0]
PIONEER_4PC_CD_AT_2 = 0.08  # Set 117 RequireNum=4 [1]
PIONEER_4PC_CD_AT_3 = 0.12  # Set 117 RequireNum=4 [2]
PIONEER_4PC_DEBUFF_THRESH_2 = 2.0
PIONEER_4PC_DEBUFF_THRESH_3 = 3.0
PIONEER_4PC_DOUBLE_DURATION = 1.0  # [5] turns after wearer applies debuff

SACERDOS_4PC_CD = 0.18  # Set 121 RequireNum=4 [0]
SACERDOS_4PC_DURATION = 2.0
SACERDOS_4PC_MAX_STACKS = 2.0

KEEL_RES_THRESHOLD = 0.30  # Set 310 [1]
KEEL_TEAM_CD = 0.10  # Set 310 [2]

PRISONER_4PC_DEF_IGNORE_PER_DOT = 0.06  # Set 116 RequireNum=4 [0]
PRISONER_4PC_MAX_DOTS = 3.0  # [1]

KNIGHT_4PC_SHIELD_ABSORB = 0.20  # Set 103 RequireNum=4 [0] — (c), damage N/A


@dataclass(frozen=True)
class RelicConditionalNote:
    set_id: str
    kind: str  # b | c
    consumed: bool
    note: str


def paper_relic_action_buffs(
    panel_chars: Sequence[Character],
    *,
    notes: list[str],
    assume_enemy_debuff_count: float = 3.0,
    assume_enemy_dot_count: float = 1.0,
    pioneer_double_coverage: float = 1.0,
) -> tuple[list[ActionBuff], list[RelicConditionalNote]]:
    """L1 ActionBuffs for acheron_direct relic (b)/(c).

    Control A assumptions (declared, not invented silently):
    - Enemy always has ≥3 debuffs (ashen + mirage + unarmored/knot density).
    - Enemy DoT count = 1 (ashen burn) unless caller overrides.
    - Pioneer 4pc double window coverage ≈1 under Acheron debuff apply rate.
    """
    out: list[ActionBuff] = []
    registry: list[RelicConditionalNote] = []
    by_id = {c.id: c for c in panel_chars}

    # --- Pioneer 117 on Acheron (merge 2pc+4pc into one buff to save combo slots) ---
    if "acheron" in by_id:
        base_cd = (
            PIONEER_4PC_CD_AT_3
            if assume_enemy_debuff_count >= PIONEER_4PC_DEBUFF_THRESH_3
            else (
                PIONEER_4PC_CD_AT_2
                if assume_enemy_debuff_count >= PIONEER_4PC_DEBUFF_THRESH_2
                else 0.0
            )
        )
        cd = base_cd * (1.0 + max(0.0, min(1.0, float(pioneer_double_coverage))))
        out.append(
            ActionBuff(
                id="relic_pioneer_117",
                applier_id="acheron",
                recipient_id="acheron",
                source_action="basic",
                duration_turns=1.0,
                dmg_boost=float(PIONEER_2PC_DMG),
                crit_dmg=float(cd),
                force_coverage=1.0,
            )
        )
        notes.append(
            f"L1_CONSUMES:relic_pioneer_117 2pc dmg_boost={PIONEER_2PC_DMG} "
            f"+ 4pc crit_dmg={cd:.4f}（≥3 debuff + double_cov={pioneer_double_coverage}；"
            f"ParamList 2pc[0]=0.12 / 4pc[2]=0.12）"
        )
        registry.append(
            RelicConditionalNote(
                "pioneer_117", "b", True, "2pc DMG + 4pc CD merged"
            )
        )

    # --- Sacerdos 121 on Sparkle → Acheron CD stacks ---
    if "sparkle" in by_id and "acheron" in by_id:
        cd = SACERDOS_4PC_CD * SACERDOS_4PC_MAX_STACKS
        out.append(
            ActionBuff(
                id="relic_sacerdos_4pc_cd",
                applier_id="sparkle",
                recipient_id="acheron",
                source_action="skill",
                duration_turns=float(SACERDOS_4PC_DURATION),
                crit_dmg=float(cd),
            )
        )
        notes.append(
            f"L1_CONSUMES:relic_sacerdos_121 4pc crit_dmg={cd:.4f} "
            f"({SACERDOS_4PC_CD}×{SACERDOS_4PC_MAX_STACKS} stacks；"
            f"ParamList[0]=0.18,[1]=2 dur,[2]=2 max；战技/终结技给单体)"
        )
        registry.append(
            RelicConditionalNote(
                "sacerdos_121", "b", True, "4pc +18% CD ×2 stacks on skill target"
            )
        )

    # --- Broken Keel 310 on Sparkle + Aventurine → team CD if RES≥30% ---
    # Merge multi-wearer CD into one ActionBuff per ally (combo-buff budget).
    keel_cd_total = 0.0
    keel_wearers: list[str] = []
    for wearer in ("sparkle", "aventurine"):
        ch = by_id.get(wearer)
        if ch is None:
            continue
        res = float(ch.build.stats.effect_res)
        if res + 1e-9 < KEEL_RES_THRESHOLD:
            notes.append(
                f"L1_CONSUMES:relic_broken_keel_310@{wearer} RES={res:.3f}<"
                f"{KEEL_RES_THRESHOLD} → team CD 未触发"
            )
            registry.append(
                RelicConditionalNote(
                    "broken_keel_310",
                    "b",
                    True,
                    f"{wearer} RES below threshold; no team CD",
                )
            )
            continue
        keel_cd_total += float(KEEL_TEAM_CD)
        keel_wearers.append(wearer)
        notes.append(
            f"L1_CONSUMES:relic_broken_keel_310@{wearer} RES={res:.3f}≥"
            f"{KEEL_RES_THRESHOLD} → team crit_dmg+{KEEL_TEAM_CD} "
            f"(ParamList[1]=0.30,[2]=0.10)"
        )
        registry.append(
            RelicConditionalNote(
                "broken_keel_310", "b", True, f"{wearer} RES≥30% team +10% CD"
            )
        )
    if keel_cd_total > 0:
        applier = keel_wearers[0]
        for ally in panel_chars:
            out.append(
                ActionBuff(
                    id=f"relic_keel_cd__team__{ally.id}",
                    applier_id=applier,
                    recipient_id=ally.id,
                    source_action="basic",
                    duration_turns=1.0,
                    crit_dmg=float(keel_cd_total),
                    force_coverage=1.0,
                )
            )
        notes.append(
            f"L1_CONSUMES:relic_broken_keel_310 merged wearers={keel_wearers} "
            f"team_cd={keel_cd_total:.2f}"
        )

    # --- Prisoner 116 on Jiaoqiu ---
    if "jiaoqiu" in by_id:
        dots = min(float(PRISONER_4PC_MAX_DOTS), float(assume_enemy_dot_count))
        ign = float(PRISONER_4PC_DEF_IGNORE_PER_DOT) * dots
        out.append(
            ActionBuff(
                id="relic_prisoner_4pc_def_ignore",
                applier_id="jiaoqiu",
                recipient_id="jiaoqiu",
                source_action="basic",
                duration_turns=1.0,
                def_reduction=float(ign),  # L1: same Def zone as ignore for formula
                force_coverage=1.0,
            )
        )
        notes.append(
            f"L1_CONSUMES:relic_prisoner_116 4pc def_ignore≈{ign:.4f} "
            f"({PRISONER_4PC_DEF_IGNORE_PER_DOT}×{dots} DoTs；"
            f"ParamList[0]=0.06,[1]=3；对照 A DoT=烬煨1)"
        )
        registry.append(
            RelicConditionalNote(
                "prisoner_116", "b", True, "4pc DEF ignore per DoT"
            )
        )

    # --- Knight 103 4pc (c): shield absorb — damage model N/A ---
    if "aventurine" in by_id:
        notes.append(
            f"L1_CONSUMES:relic_knight_103 4pc shield_absorb+{KNIGHT_4PC_SHIELD_ABSORB} "
            "（(c) 护盾吸收；本模型无血量/承伤，伤害公式 N/A，登记为已消费）"
        )
        registry.append(
            RelicConditionalNote(
                "knight_103",
                "c",
                True,
                "4pc shield absorb; damage N/A",
            )
        )

    # Izumo same-path CR is orchestration-layer (a)/(b) already in benchmark.
    notes.append(
        "L1_CONSUMES:relic_izumo_314 same-path CR 已在 loadout/benchmark 编排层消费"
    )
    registry.append(
        RelicConditionalNote(
            "izumo_314", "b", True, "same-path +12% CR (orchestration)"
        )
    )
    return out, registry


def collect_relic_dmg_boost_l2(
    attacker: Character,
    *,
    enemy_debuff_count: int,
) -> float:
    """L2 outgoing: Pioneer 2pc when enemy has ≥1 debuff."""
    # Detect via loadout notes / character id convention: acheron wears pioneer.
    if attacker.id != "acheron":
        return 0.0
    if enemy_debuff_count <= 0:
        return 0.0
    return float(PIONEER_2PC_DMG)


def collect_relic_crit_dmg_l2(
    attacker: Character,
    *,
    enemy_debuff_count: int,
    pioneer_doubled: bool,
) -> float:
    """L2: Pioneer 4pc CD vs ≥2/3 debuffs (+ double)."""
    if attacker.id != "acheron":
        return 0.0
    if enemy_debuff_count >= int(PIONEER_4PC_DEBUFF_THRESH_3):
        base = PIONEER_4PC_CD_AT_3
    elif enemy_debuff_count >= int(PIONEER_4PC_DEBUFF_THRESH_2):
        base = PIONEER_4PC_CD_AT_2
    else:
        return 0.0
    return float(base) * (2.0 if pioneer_doubled else 1.0)


def collect_relic_def_ignore_l2(
    attacker: Character,
    *,
    enemy_dot_count: int,
) -> float:
    """L2: Prisoner 4pc DEF ignore."""
    if attacker.id != "jiaoqiu":
        return 0.0
    n = min(int(PRISONER_4PC_MAX_DOTS), max(0, int(enemy_dot_count)))
    return float(PRISONER_4PC_DEF_IGNORE_PER_DOT) * float(n)


def keel_team_cd_if_eligible(wearer: Character) -> float:
    if float(wearer.build.stats.effect_res) + 1e-9 < KEEL_RES_THRESHOLD:
        return 0.0
    return float(KEEL_TEAM_CD)
