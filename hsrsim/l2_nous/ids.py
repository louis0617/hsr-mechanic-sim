"""ID maps: our slug / loadout → HSR_Nous fixture template ids."""
from __future__ import annotations

# our character_id → Nous AvatarID string (fixture prefix)
AVATAR_ID: dict[str, str] = {
    "acheron": "1308",
    "jiaoqiu": "1218",
    "sparkle": "1306",
    "aventurine": "1304",
    "pela": "1106",
    "mortenaxblade": "1507",
    "castorice": "1407",
    "hyacine": "1409",
    "evernight": "1413",
    "cyrene": "1415",
}

# our character_id → signature LC id (S1)
LC_ID: dict[str, int] = {
    "acheron": 23024,
    "jiaoqiu": 23029,
    "sparkle": 23021,
    "aventurine": 23023,
    "pela": 21015,
    "mortenaxblade": 23059,
    "castorice": 23040,
    "hyacine": 23042,
    "evernight": 23049,
    "cyrene": 23052,
    "robin_summeretto": 23063,  # 知更鸟•晴歌专武（非 23064）
}

# our main/sub key → Nous relic affix id (_AFFIX_FIELD keys)
AFFIX: dict[str, str] = {
    "hp_flat": "hp",
    "atk_flat": "atk",
    "def_flat": "def_",
    "hp_pct": "hp_pct",
    "atk_pct": "atk_pct",
    "def_pct": "def_pct",
    "speed": "spd",
    "crit_rate": "crit_rate",
    "crit_dmg": "crit_dmg",
    "ehr": "effect_hit",
    "effect_res": "effect_res",
    "err": "energy_regen",
    "lightning_dmg": "thunder_dmg",
    "fire_dmg": "fire_dmg",
    "ice_dmg": "ice_dmg",
    "imaginary_dmg": "imaginary_dmg",
    "quantum_dmg": "quantum_dmg",
    "wind_dmg": "wind_dmg",
    "physical_dmg": "physical_dmg",
}


def set_slug_to_nous_id(slug: str) -> str:
    """pioneer_117 → 117; messenger_114 → 114; broken_keel_310 → 310.

    Note: RelicSet 108 is Genius (量子), not Messenger. Use ``messenger_114``.
    """
    if "_" not in slug:
        return slug
    tail = slug.rsplit("_", 1)[-1]
    if not tail.isdigit():
        raise ValueError(f"cannot parse set id from slug {slug!r}")
    return tail
