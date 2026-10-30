"""Mortenax Blade (千冶·刃) constants from AvatarSkillConfig ParamList L10/L6.

Leave-out: original EN TextMap + ParamList only. Not Fribbels as GT.
"""
from __future__ import annotations

MORTENAX_ID = "mortenaxblade"
ZONE_ID = "mortenax_ult_zone"
FURY_ID = "mortenax_infinite_fury"
BALEFIRE_ID = "mortenax_balefire_bind"
ZONE_DMG_ID = "mortenax_zone_team_dmg"
ZONE_ULT_DMG_ID = "mortenax_zone_team_ult_dmg"
ZONE_SELF_DMG_ID = "mortenax_zone_self_dmg"
PURGATORY_ID = "lc_23059_purgatory"
CHARGE_VAR = "mortenax_charge"
LC_ENERGY_ONCE_VAR = "_lc_23059_turn_energy_done"
SKILL_ACTION_ID = "mortenax_skill"
SKILL_FUA_ACTION_ID = "mortenax_skill_fua"
BIND_ULT_ID = "mortenax_ult_bind"
TENAX_ULT_ID = "mortenax_ult_tenax"
BASIC_ID = "mortenax_basic"
BASIC_FURY_ID = "mortenax_basic_fury"

# 150702 L10
SKILL_HP_COST = 0.10
SKILL_AOE_MV = 0.72
SKILL_BOUNCE_N = 4
SKILL_BOUNCE_MV = 0.24
# 150703 L10
ULT_HP_COST = 0.20
FURY_CR = 0.20
FURY_CD = 0.60
BALEFIRE_VULN = 0.50
BALEFIRE_DEF_SHRED = 0.30
BALEFIRE_TURNS = 2
COUNTDOWN_SPD = 70.0
LETHAL_HEAL = 0.50
# 150701/08 L6 (E0)
BASIC_MV = 0.50
BASIC_FURY_MV = 1.00
# 150714 L10
TENAX_MV = 3.50
# 150704 L10
CHARGE_NEED = 9
TALENT_ENERGY = 25.0
# 1507103
ZONE_ALLY_DMG = 0.50
OTHER_NIHILITY_ULT_DMG = 0.75
SOLO_SELF_DMG = 0.75
# 1507101 ParamList [0.75, 80]
ENERGY_FLOOR = 0.75
OVERFLOW_ENERGY_CAP = 80.0
OVERFLOW_VAR = "mortenax_overflow_energy"
# 1507102
ZONE_AGGRO_MULT = 10.0
ZONE_MIT = 0.50
ZONE_HEAL_TAKEN = 0.50

# LC 23059 S1 (filled from EquipmentSkillConfig in rebuild script notes)
LC_ID = 23059
LC_HP_PCT_S1 = 0.30
LC_TURN_ENERGY_S1 = 20.0
# Purgatory: duration / crit_dmg taken / extra from wearer — ParamList slots verified at rebuild.

COUNTDOWN_AV = 10000.0 / COUNTDOWN_SPD
