""" L1: Jiaoqiu zone coverage, ashen steady stacks, zone dream flow."""
from __future__ import annotations

from typing import Mapping

from hsrsim.enemies.benchmark_dummy import DUMMY_SPEED
from hsrsim.simulator.ehr import debuff_hit_chance
from hsrsim.simulator.field_zone import (
    JIAOQIU_ZONE_DURATION,
    JIAOQIU_ZONE_MAX_TRIGGERS,
    JIAOQIU_ZONE_PROC_CHANCE,
    JIAOQIU_ZONE_ULT_VULN,
)

ASHEN_MAX_STACKS = 5.0
ASHEN_DURATION_TURNS = 2.0
ASHEN_VULN_AT_ONE = 0.15
ASHEN_VULN_PER_EXTRA = 0.05


def zone_coverage(
    jq_rates: Mapping[str, float],
    *,
    duration_turns: float = JIAOQIU_ZONE_DURATION,
) -> float:
    """结界覆盖率：每次终结技开 3 回合（椒丘回合递减）。

    cov = min(1, u × duration / (nb+ns+u)).
    """
    nb = float(jq_rates.get("nb", jq_rates.get("basic", 0.0)))
    ns = float(jq_rates.get("ns", jq_rates.get("skill", 0.0)))
    u = float(jq_rates.get("u", jq_rates.get("ult", 0.0)))
    turns = nb + ns + u
    if turns <= 0.0 or u <= 0.0:
        return 0.0
    return min(1.0, u * float(duration_turns) / turns)


def zone_proc_hit_chance(
    *,
    ehr: float,
    enemy_effect_res: float,
    base: float = JIAOQIU_ZONE_PROC_CHANCE,
) -> float:
    return float(debuff_hit_chance(base, ehr, enemy_effect_res))


def zone_dream_rate(
    *,
    enemy_speed: float = DUMMY_SPEED,
    zone_cov: float,
    hit_chance: float,
    jq_ult_rate: float,
    counts_for_dream: bool = True,
    max_triggers: float = JIAOQIU_ZONE_MAX_TRIGGERS,
) -> dict[str, float]:
    """结界残梦流量 /100AV（含每周期 6 次上限 ∩ 每敌每回合 1）。

    λ_raw = enemy_action_rate × hit × cov
    λ_cap_reset = max_triggers × jq_ult_rate   # 每次开大重置 6
    λ_cap_per_enemy_turn = enemy_action_rate × cov  # 单敌每回合最多 1
    λ = min(λ_raw, λ_cap_reset, λ_cap_per_enemy_turn) if counts else 0
    """
    enemy_r = float(enemy_speed) / 100.0
    raw = enemy_r * float(hit_chance) * float(zone_cov)
    cap_reset = float(max_triggers) * float(jq_ult_rate)
    cap_per = enemy_r * float(zone_cov)
    capped = min(raw, cap_reset, cap_per) if float(zone_cov) > 0 else 0.0
    final = capped if counts_for_dream else 0.0
    return {
        "enemy_action_rate": enemy_r,
        "hit_chance": float(hit_chance),
        "zone_cov": float(zone_cov),
        "raw": raw,
        "cap_reset": cap_reset,
        "cap_per_enemy_turn": cap_per,
        "rate": final,
        "counts_for_dream": float(1.0 if counts_for_dream else 0.0),
        "ult_vuln": float(JIAOQIU_ZONE_ULT_VULN),
    }


def expected_ashen_stacks(
    jq_rates: Mapping[str, float],
    *,
    zone_proc_rate: float = 0.0,
    max_stacks: float = ASHEN_MAX_STACKS,
    duration_turns: float = ASHEN_DURATION_TURNS,
    enemy_speed: float = DUMMY_SPEED,
) -> dict[str, float]:
    """烬煨稳态期望层数（按动作贡献拆分）。

    施加速率：普攻 1 + 战技 2（天赋+战技）+ 终结技 1 + 结界 proc。
    E[s] = min(max, apply × duration / enemy_turns)。
    """
    nb = float(jq_rates.get("nb", jq_rates.get("basic", 0.0)))
    ns = float(jq_rates.get("ns", jq_rates.get("skill", 0.0)))
    u = float(jq_rates.get("u", jq_rates.get("ult", 0.0)))
    from_basic = nb * 1.0
    from_skill = ns * 2.0
    from_ult = u * 1.0
    from_zone = float(zone_proc_rate)
    apply = from_basic + from_skill + from_ult + from_zone
    enemy_turns = float(enemy_speed) / 100.0
    raw = apply * float(duration_turns) / enemy_turns if enemy_turns > 0 else 0.0
    stacks = min(float(max_stacks), raw)
    vuln = 0.0
    if stacks > 0:
        vuln = float(ASHEN_VULN_AT_ONE) + max(0.0, stacks - 1.0) * float(
            ASHEN_VULN_PER_EXTRA
        )
    return {
        "from_basic": from_basic,
        "from_skill": from_skill,
        "from_ult": from_ult,
        "from_zone": from_zone,
        "apply_rate": apply,
        "raw_load": raw,
        "stacks": stacks,
        "vuln": vuln,
        "max_stacks": float(max_stacks),
    }
