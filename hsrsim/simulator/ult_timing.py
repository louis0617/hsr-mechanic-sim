"""F0-lite: Acheron critical-action Sparkle skill buff coverage (shared L1/L2)."""
from __future__ import annotations

from enum import Enum


class UltTimingStrategy(str, Enum):
    """开大时机（对账时 L1/L2 必须同一策略）。

    immediate_when_full: 残梦满（资源就绪）即插入终结技。
    wait_sparkle_skill_buff: 等花火战技增益在自身后再开大（本出口仅 L2 探索）。
    """

    IMMEDIATE_WHEN_FULL = "immediate_when_full"
    WAIT_SPARKLE_SKILL_BUFF = "wait_sparkle_skill_buff"


SPARKLE_SKILL_BUFF_ID = "sparkle_skill_buff"

# Control A L2 实测：黄泉终结技/普攻命中时刻花火战技覆盖 ≈0.723。
# 策略参数（非 min(1,λ) 整体覆盖）。
IMMEDIATE_SPARKLE_AT_ACTION = {
    "ult": 0.72,
    "basic": 0.72,
    "skill": 0.72,
}


def sparkle_buff_coverage_at_action(
    strategy: UltTimingStrategy | str,
    action: str,
    *,
    lambda_coverage: float,
    immediate_by_action: dict[str, float] | None = None,
) -> float:
    """黄泉关键动作时刻的花火战技增益覆盖 — 由策略参数给定。

    wait → 1.0（该策略下只在有增益时开大；普攻/战技仍用策略表或 λ）
    immediate → ``IMMEDIATE_SPARKLE_AT_ACTION[action]``（ 实测）
    search_measured → 必须传入 ``immediate_by_action``（F5：由搜索局实测覆盖）
    """
    s = UltTimingStrategy(strategy) if strategy != "search_measured" else strategy
    act = str(action)
    if act not in ("basic", "skill", "ult"):
        raise ValueError(f"action must be basic/skill/ult, got {act!r}")
    if strategy == "search_measured" or (
        isinstance(strategy, str) and strategy == "search_measured"
    ):
        if not immediate_by_action or act not in immediate_by_action:
            raise ValueError(
                "search_measured requires immediate_by_action covering basic/skill/ult"
            )
        return max(0.0, min(1.0, float(immediate_by_action[act])))
    s = UltTimingStrategy(strategy)
    if s is UltTimingStrategy.WAIT_SPARKLE_SKILL_BUFF:
        # Ult gated to buffed window → 1.0; other actions keep λ until F models hold.
        if act == "ult":
            return 1.0
        return max(0.0, min(1.0, float(lambda_coverage)))
    table = dict(IMMEDIATE_SPARKLE_AT_ACTION)
    if immediate_by_action:
        table.update({k: float(v) for k, v in immediate_by_action.items()})
    return max(0.0, min(1.0, float(table[act])))


def sparkle_buff_coverage_at_ult(
    strategy: UltTimingStrategy | str,
    *,
    lambda_coverage: float,
    immediate_at_ult: float | None = None,
) -> float:
    """Compat wrapper → ``sparkle_buff_coverage_at_action(..., \"ult\")``."""
    override = {"ult": immediate_at_ult} if immediate_at_ult is not None else None
    return sparkle_buff_coverage_at_action(
        strategy,
        "ult",
        lambda_coverage=lambda_coverage,
        immediate_by_action=override,
    )
