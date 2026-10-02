"""Path → BaseAggro (taunt weight) from datamine AvatarPromotionConfig.

Source: ``AvatarPromotionConfig.BaseAggro`` grouped by
``AvatarConfig.AvatarBaseType`` (commit-local turnbasedgamedata). Empirically
constant per path:

| AvatarBaseType | Path (本仓) | BaseAggro |
|---|---|---:|
| Knight | preservation | 150 |
| Warrior | destruction | 125 |
| Mage | erudition | 75 |
| Rogue | the_hunt | 75 |
| Priest | abundance | 100 |
| Shaman | harmony | 100 |
| Warlock | nihility | 100 |
| Memory | remembrance | 100 |
| Elation | elation | 100 |

Hit probability for ally i under standard dummy (one hit per enemy action):
``p_i = BaseAggro_i / Σ BaseAggro``. L1 uses this expectation (no RNG).
"""
from __future__ import annotations

from typing import Sequence

from hsrsim.simulator.types import Character, Path

# Datamine AvatarBaseType → BaseAggro (AvatarPromotionConfig Promotion=0).
PATH_BASE_AGGRO: dict[str, float] = {
    Path.PRESERVATION.value: 150.0,
    Path.DESTRUCTION.value: 125.0,
    Path.ERUDITION.value: 75.0,
    Path.THE_HUNT.value: 75.0,
    Path.ABUNDANCE.value: 100.0,
    Path.HARMONY.value: 100.0,
    Path.NIHILITY.value: 100.0,
    Path.REMEMBRANCE.value: 100.0,
    Path.ELATION.value: 100.0,
}

AGGRO_SOURCE = (
    "data/external/turnbasedgamedata/ExcelOutput/AvatarPromotionConfig.json"
    " (BaseAggro) × AvatarConfig.AvatarBaseType"
)


def path_key(path: object) -> str:
    if isinstance(path, Path):
        return path.value
    return str(path)


def base_aggro(char: Character) -> float:
    """Taunt weight for one ally. Unknown path → 100 (datamine default band)."""
    return float(PATH_BASE_AGGRO.get(path_key(char.path), 100.0))


def hit_share_table(allies: Sequence[Character]) -> dict[str, float]:
    """Expected hit probability per ally id (sums to 1 if any alive-weight > 0)."""
    weights = {c.id: base_aggro(c) for c in allies}
    total = sum(weights.values())
    if total <= 0.0:
        n = len(weights)
        if n == 0:
            return {}
        return {cid: 1.0 / n for cid in weights}
    return {cid: w / total for cid, w in weights.items()}


def pick_weighted_ally(
    allies: Sequence[Character],
    rng,
) -> Character | None:
    """Weighted random among alive allies by BaseAggro. None if none alive."""
    alive = [a for a in allies if getattr(a, "is_alive", True)]
    # CharacterState has is_alive; Character alone is always "alive".
    if not alive:
        return None
    # Accept CharacterState (has .char) or Character.
    units = []
    weights = []
    for u in alive:
        char = getattr(u, "char", u)
        units.append(u)
        weights.append(base_aggro(char))
    total = sum(weights)
    if total <= 0.0:
        return units[0]
    roll = rng.random() * total
    acc = 0.0
    for u, w in zip(units, weights):
        acc += w
        if roll <= acc:
            return u
    return units[-1]
