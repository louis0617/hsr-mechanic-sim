"""Read C0 scalings and combo templates from local fribbels/hsr-optimizer."""
from hsrsim.external.fribbels.paths import FRIBBELS_ROOT, character_ts_path
from hsrsim.external.fribbels.parser import (
    CharacterFribbelsData,
    evernight_memo_hp_scaling,
    hyacine_heal_tally_scaling,
    load_character_fribbels,
    parse_c0_scalings,
    parse_combo_turn_abilities,
)

__all__ = [
    "FRIBBELS_ROOT",
    "CharacterFribbelsData",
    "character_ts_path",
    "evernight_memo_hp_scaling",
    "hyacine_heal_tally_scaling",
    "load_character_fribbels",
    "parse_c0_scalings",
    "parse_combo_turn_abilities",
]
