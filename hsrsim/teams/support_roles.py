"""Infer support role from character build for SupportBot."""
from __future__ import annotations

from hsrsim.simulator.types import Character, EffectTarget

SupportRole = str  # def_shred | dmg_amp | sustain

_DEF_STATS = frozenset({"def_reduction", "def_ignore", "res"})
_VULN_STATS = frozenset({"vuln", "vuln_apply"})
_AMP_STATS = frozenset({"dmg_boost", "crit_rate", "crit_dmg", "original_mult", "super_break_boost"})


def infer_support_role(char: Character) -> SupportRole:
    """Classify a support character for fixed-axis SupportBot."""
    enemy_debuff = False
    ally_buff = False

    for eff in char.build.effects:
        stats = {m.target_stat.split(".", 1)[0] for m in eff.modifiers}
        target = eff.target
        on_enemy = target in (EffectTarget.SINGLE_ENEMY, EffectTarget.ALL_ENEMIES)
        on_ally = target in (EffectTarget.SELF, EffectTarget.SINGLE_ALLY, EffectTarget.ALL_ALLIES)
        if on_enemy and (stats & (_DEF_STATS | _VULN_STATS)):
            enemy_debuff = True
        if on_ally and (stats & _AMP_STATS):
            ally_buff = True

    for action in char.build.actions:
        if action.applies_effects and action.effect_target == "enemy":
            enemy_debuff = True
        if action.applies_effects and action.effect_target in ("self", "all_allies"):
            ally_buff = True

    if enemy_debuff:
        return "def_shred"
    if ally_buff:
        return "dmg_amp"
    return "sustain"
