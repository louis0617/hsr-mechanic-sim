"""KQM damage helpers ported from Fribbels damageCalculator.ts (C0 reference)."""
from __future__ import annotations

# Fribbels: const cLevelConst = 20 + 80
C_LEVEL_CONST = 100.0


def calculate_def_multi(enemy_level: int, def_pen: float) -> float:
    """Same as Fribbels calculateDefMulti / WGSL defMulti."""
    return C_LEVEL_CONST / (
        (enemy_level + 20) * max(0.0, 1.0 - def_pen) + C_LEVEL_CONST
    )


def calculate_res_multi(enemy_res: float, res_pen: float) -> float:
    return 1.0 - (enemy_res - res_pen)


def expected_crit_multiplier(crit_rate: float, crit_dmg: float) -> float:
    """Fribbels getCritMultiplier."""
    cr = min(1.0, crit_rate)
    return cr * (1.0 + crit_dmg) + (1.0 - cr)


def base_universal_multi(*, weakness_broken: bool) -> float:
    return 1.0 if weakness_broken else 0.9
