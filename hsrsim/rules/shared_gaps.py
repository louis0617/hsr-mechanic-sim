"""Shared-gap (S-class) traces present in both L1 and L2 when enabled.

S1 = Jiaoqiu skill-tree 1218102 Hearth Kindle (EHR → ATK%)
S2 = Sparkle skill-tree 11306102 Artificial Flower (3 SP / turn → next Skill free)

Evidence: StarRailRes skill-tree text + AvatarSkillTreeConfig ParamList.
Simulator self-measurements are not evidence for the formulas.
"""
from __future__ import annotations

import math

# --- S1: 1218102 Hearth Kindle ---
# CN: 椒丘效果命中大于#1[i]%时，每超过#2[i]%，则额外提高#3[i]%攻击力，最高不超过#4[i]%。
# EN: For every #2[i]% of Jiaoqiu's Effect Hit Rate that exceeds #1[i]%,
#     additionally increases ATK by #3[i]%, up to #4[i]%.
# ParamList [0.8, 0.15, 0.6, 2.4] — AvatarSkillTreeConfig PointID 1218102
JIAOQIU_1218102_EHR_FLOOR = 0.8
JIAOQIU_1218102_EHR_STEP = 0.15
JIAOQIU_1218102_ATK_PER_STEP = 0.6
JIAOQIU_1218102_ATK_CAP = 2.4
JIAOQIU_1218102_EFFECT_ID = "jiaoqiu_hearth_kindle"

# --- S2: 11306102 Artificial Flower (enhanced) ---
# CN: 若我方角色单回合内消耗的战技点大于等于#1[i]，则花火下一次施放战技时不会消耗战技点。
# EN: If an ally character consumes #1[i] or more Skill Points in a single turn,
#     Sparkle's next use of Skill will not consume Skill Points.
# ParamList [3] — AvatarSkillTreeConfig PointID 11306102
SPARKLE_11306102_SP_THRESHOLD = 3
SPARKLE_11306102_FREE_VAR = "sparkle_next_skill_free"
SPARKLE_11306102_TURN_SP_VAR = "_ally_turn_sp_consumed"


def jiaoqiu_hearth_kindle_atk_pct(ehr: float) -> float:
    """Return ATK% bonus in [0, 2.4] from EHR (fraction, not percent)."""
    if ehr <= JIAOQIU_1218102_EHR_FLOOR + 1e-12:
        return 0.0
    # +1e-9 guards float residual e.g. (0.95-0.8)/0.15 → 0.999… 
    steps = int(
        math.floor(
            (float(ehr) - JIAOQIU_1218102_EHR_FLOOR) / JIAOQIU_1218102_EHR_STEP
            + 1e-9
        )
    )
    if steps <= 0:
        return 0.0
    return min(
        JIAOQIU_1218102_ATK_CAP,
        JIAOQIU_1218102_ATK_PER_STEP * float(steps),
    )


def jiaoqiu_hearth_kindle_atk_flat(ehr: float, base_atk: float) -> float:
    """Flat ATK = atk_pct × promotion/base ATK (Fribbels conversion base)."""
    return jiaoqiu_hearth_kindle_atk_pct(ehr) * float(base_atk)
