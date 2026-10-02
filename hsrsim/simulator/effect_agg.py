"""Aggregate EffectModifier bags into flat stat deltas for damage computation."""
from __future__ import annotations

from collections import defaultdict

from hsrsim.simulator.types import Effect, Element

ATTACKER_STATS = frozenset(
    {
        "dmg_boost",
        "crit_rate",
        "crit_dmg",
        "res_pen",
        "break_effect",
        "super_break_boost",
        "super_break_dmg",
        "def_ignore",
        "elation",
        "punchline",
        "merrymake",
        "original_mult",
        "true_followup",
        "atk",
        "speed",
    }
)
DEFENDER_STATS = frozenset(
    {
        "def_reduction",
        "vuln_apply",
        "vuln",
        "weaken",
        "res",
        "mit",
    }
)


SPARKLE_FIGMENT_ID = "sparkle_figment"
SPARKLE_CIPHER_ID = "sparkle_enemy_vuln"


def figment_aura_vuln(
    *,
    attacker_effects: list[Effect],
    ally_effects_bags: list[list[Effect]],
) -> float:
    """Sparkle figment stacks → enemy vuln aura; cipher bonus if attacker holds it.

    : figment lives on Sparkle (ally). Enemies are never inflicted.
    Cipher (谜诡) is an ally buff; only the attacker having it raises per-stack
    talent vuln.
    """
    figment: Effect | None = None
    for bag in ally_effects_bags:
        for effect in bag:
            if effect.id == SPARKLE_FIGMENT_ID:
                figment = effect
                break
        if figment is not None:
            break
    if figment is None or float(figment.current_stacks) <= 0:
        return 0.0
    base = _effect_vuln_add(figment)
    bonus = 0.0
    for effect in attacker_effects:
        if effect.id == SPARKLE_CIPHER_ID:
            bonus = _effect_vuln_add(effect)
            break
    return float(figment.current_stacks) * (base + bonus)


def aggregate_effects(effects: list[Effect], *, side: str) -> dict[str, float]:
    """Sum modifiers from active effects, filtered by combat role.

    side='attacker': dmg_boost, crit, res_pen, break_effect, etc. on the hitter
    side='defender': def_reduction, vuln, weaken on the target being hit

    : sparkle_figment / sparkle_enemy_vuln are not read here as defender
    debuffs. Use ``figment_aura_vuln`` (holder stacks + attacker cipher).
    """
    allowed = ATTACKER_STATS if side == "attacker" else DEFENDER_STATS
    totals: dict[str, float] = defaultdict(float)
    for effect in effects:
        # Figment/cipher: aura path only (never flat on defender bag).
        if effect.id in (SPARKLE_FIGMENT_ID, SPARKLE_CIPHER_ID):
            continue
        if side == "defender" and _ashen_roast_vuln(effect) is not None:
            totals["vuln"] += float(_ashen_roast_vuln(effect))
            continue
        stacks = float(effect.current_stacks)
        for mod in effect.modifiers:
            if mod.operation != "add":
                continue
            base_stat = mod.target_stat.split(".", 1)[0]
            if base_stat not in allowed:
                continue
            totals[mod.target_stat] += mod.value * stacks
    return dict(totals)


def _ashen_roast_vuln(effect: Effect) -> float | None:
    """Ashen Roast: vuln = at_one + (stacks-1)*per_extra ( talent L10)."""
    if effect.vuln_at_one_stack is None or effect.vuln_per_extra_stack is None:
        return None
    stacks = float(effect.current_stacks)
    if stacks <= 0:
        return 0.0
    return float(effect.vuln_at_one_stack) + (stacks - 1.0) * float(
        effect.vuln_per_extra_stack
    )


def _effect_vuln_add(effect: Effect) -> float:
    return sum(
        float(mod.value)
        for mod in effect.modifiers
        if mod.target_stat == "vuln" and mod.operation == "add"
    )


def aggregate_effect_modifiers(
    effects: list[Effect],
    element: Element,
) -> dict[str, float]:
    """Legacy: aggregate all modifiers without side filter."""
    totals: dict[str, float] = defaultdict(float)
    for effect in effects:
        stacks = float(effect.current_stacks)
        for mod in effect.modifiers:
            if mod.operation != "add":
                continue
            totals[mod.target_stat] += mod.value * stacks
    _ = element
    return dict(totals)


def stat_bonus_for_element(totals: dict[str, float], prefix: str, element: Element) -> float:
    """Read `prefix.all` + `prefix.<element>` additive bonuses."""
    return totals.get(f"{prefix}.all", 0.0) + totals.get(f"{prefix}.{element.value}", 0.0)
