""" L1 expectation helpers: hit shares + Aventurine Blind Bet / FUA rate."""
from __future__ import annotations

from typing import Sequence

from hsrsim.enemies.benchmark_dummy import DUMMY_SPEED
from hsrsim.enemies.path_base_aggro import hit_share_table
from hsrsim.simulator.types import Character

BLIND_BET_FUA_COST = 7.0
# Ult ParamList #1: random 1..7 Blind Bet → expectation 4.
ULT_BLIND_BET_EXPECT = 4.0
# Extra Blind Bet ICD duty approx when Aventurine is hit while holding chip.
EXTRA_BLIND_DUTY = 0.5


def expected_enemy_action_rate(*, enemy_speed: float = DUMMY_SPEED) -> float:
    """Enemy normal actions per 100 AV."""
    return float(enemy_speed) / 100.0


def expected_blind_bet_gain_rate(
    allies: Sequence[Character],
    *,
    enemy_speed: float = DUMMY_SPEED,
    aventurine_ult_rate: float = 0.0,
    chip_uptime: float = 1.0,
) -> dict[str, float]:
    """Steady-state Blind Bet gain /100 AV (L1 expectation).

    Per enemy action (1 hit): chip holder hit → +1; if target is Aventurine and
    extra ICD ready → +1 more (duty ≈ EXTRA_BLIND_DUTY). Ult → E[1..7]=4.
    """
    shares = hit_share_table(list(allies))
    enemy_r = expected_enemy_action_rate(enemy_speed=enemy_speed)
    aven_share = float(shares.get("aventurine", 0.0))
    from_hits = enemy_r * float(chip_uptime) * (
        1.0 + aven_share * float(EXTRA_BLIND_DUTY)
    )
    from_ult = float(aventurine_ult_rate) * ULT_BLIND_BET_EXPECT
    total = from_hits + from_ult
    return {
        "enemy_action_rate": enemy_r,
        "hit_shares": shares,
        "from_hits": from_hits,
        "from_ult": from_ult,
        "total_blind_bet": total,
        "fua_rate": total / BLIND_BET_FUA_COST,
        "chip_uptime": float(chip_uptime),
        "extra_blind_duty": EXTRA_BLIND_DUTY,
    }


def dream_external_from_fua(
    fua_rate: float, *, has_lc_23023: bool = True
) -> float:
    """R1: each FUA that lands 23023 vuln counts once (follow_up = skill cast)."""
    if not has_lc_23023:
        return 0.0
    return float(fua_rate)
