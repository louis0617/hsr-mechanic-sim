"""Derivable asserts for Acheron line — expectations from skill text / ParamList.

每黄泉回合残梦
--------------
黄泉 E2（行迹/星魂，`counter_gains` acheron_e2_turn_start）：自身回合开始 +1 残梦。
天赋 R3（战技固有）：战技 +1。
天赋 R2（施放技能期间施加负面）：战技挂集真赤/泡影 → +1；普攻挂泡影(23024) → +1。

故：
- 战技回合期望 = 1 (E2) + 1 (R3) + 1 (R2) = **3**
- 普攻回合期望 = 1 (E2) + 1 (R2) = **2**
加权：``3·σ + 2·(1−σ)``，σ = 战技占黄泉普通行动份额。

终结技/战技次均伤害比
--------------------
 §3.2 **R-MV-plus-thunder-hits**（原文 ParamList 推导，非战斗阈值）：
主目标满层消去路径 MV 合计 5.22 / 战技主目标 1.6 = **3.2625**（下限断言 ≥3.26）。
"""
from __future__ import annotations

# Own-window dream gains (E2 paper team).
DREAMS_PER_ACHERON_SKILL_TURN = 3.0
DREAMS_PER_ACHERON_BASIC_TURN = 2.0

# R-MV-plus-thunder-hits: (3.72 + 1.50) / 1.60
ULT_OVER_SKILL_MEAN_MV_RATIO_MIN = 3.26


def expected_dreams_per_acheron_turn(skill_share: float) -> float:
    """σ = skill / (basic+skill) on Acheron's normal turns."""
    s = max(0.0, min(1.0, float(skill_share)))
    return DREAMS_PER_ACHERON_SKILL_TURN * s + DREAMS_PER_ACHERON_BASIC_TURN * (1.0 - s)


def derive_dreams_per_turn_note(skill_share: float) -> str:
    e = expected_dreams_per_acheron_turn(skill_share)
    return (
        f"推导：E2回合开始+1；战技 R3+1 且 R2+1 → 战技回合 3；"
        f"普攻泡影 R2+1 → 普攻回合 2；σ={skill_share:.4f} → E[残梦/回合]={e:.4f}"
    )


def derive_ult_skill_mv_ratio_note() -> str:
    return (
        "推导： R-MV-plus-thunder-hits — 开大前集真赤 S=9 消去路径主目标 MV "
        "3.72（啼泽正文最大）+ 雷心 6×0.25=1.50 → 5.22；战技主目标 1.60；"
        f"比 5.22/1.60={ULT_OVER_SKILL_MEAN_MV_RATIO_MIN}"
    )
