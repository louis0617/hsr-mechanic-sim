"""Effect Hit Rate (EHR) apply chance — Fribbels / community formula."""
from __future__ import annotations


def debuff_hit_chance(
    base_chance: float,
    ehr: float,
    enemy_effect_res: float,
    *,
    effect_res_pen: float = 0.0,
) -> float:
    """Final chance to land a chance-based debuff.

    Matches Fribbels: min(1, base * (1+EHR) * (1 - enemyRES + RES_PEN)).
    """
    if base_chance <= 0.0:
        return 0.0
    return min(
        1.0,
        float(base_chance)
        * (1.0 + float(ehr))
        * (1.0 - float(enemy_effect_res) + float(effect_res_pen)),
    )
