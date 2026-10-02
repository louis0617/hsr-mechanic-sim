"""Benchmark training dummy — single source for L1 and L2 enemy params."""
from __future__ import annotations

import copy
from collections.abc import Iterable, Sequence

from hsrsim.simulator.types import Character, Element

# Explicit benchmark defaults ( / ).
# Fribbels optimizerFormDefaults: enemyLevel=95, enemyResistance=0.2,
# enemyEffectResistance=0.3, enemyMaxToughness=360, enemyElementalWeak=true,
# enemyWeaknessBroken=false. DefZone matches Fribbels level formula (no DEF stat).
DUMMY_ID = "lv95_dummy"
DUMMY_LEVEL = 95
DUMMY_DEFENSE = 1000.0  # nominal only; DefZone uses defender_level+20
DUMMY_SPEED = 90.0  # Fribbels has no enemy AV; fixed for our turn clock / 
DUMMY_TOUGHNESS = 360.0  # Fribbels enemyMaxToughness
DUMMY_HP = 1_000_000.0  # immortal floor only; not a fight target
DUMMY_EFFECT_RES = 0.3  # Fribbels enemyEffectResistance
# Non-weakness elemental RES; weakness attributes use 0.0.
DEFAULT_ELEMENT_RES = 0.2
#one hit per enemy action; target = BaseAggro-weighted random (L1 = expectation).
# Explicit standard-dummy assumption — not a stage AI skill.
DUMMY_HIT_TARGET_RULE = "path_base_aggro_weighted"
DUMMY_HITS_PER_ENEMY_ACTION = 1

ALL_ELEMENTS: tuple[str, ...] = tuple(e.value for e in Element)


def _elem_str(e: object) -> str:
    return e.value if isinstance(e, Element) else str(e)


def ally_element_keys(allies: Sequence[Character] | Iterable[Character]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for ally in allies:
        for el in ally.elements:
            key = _elem_str(el)
            if key not in seen:
                seen.add(key)
                out.append(key)
    return out


def element_res_table(weaknesses: Sequence[str]) -> dict[str, float]:
    weak = {_elem_str(w) for w in weaknesses}
    return {e: (0.0 if e in weak else DEFAULT_ELEMENT_RES) for e in ALL_ELEMENTS}


def element_resistance(enemy: Character, element: object) -> float:
    """RES for one hit element from the enemy's explicit ``element_res`` table."""
    key = _elem_str(element)
    table = enemy.build.stats.element_res or {}
    if key in table:
        return float(table[key])
    if "all" in table:
        return float(table["all"])
    return 0.0


def build_benchmark_dummy(
    *,
    ally_elements: Sequence[str] | None = None,
    weaknesses: Sequence[str] | None = None,
    immortal: bool = False,
) -> Character:
    """Lv95 training dummy shared by L1 adapter and L2 scenario.

    Default weaknesses = all ally damage elements; those attributes have RES 0,
    every other element has RES ``DEFAULT_ELEMENT_RES`` (0.2).
    """
    if weaknesses is not None:
        weak_list = [_elem_str(w) for w in weaknesses]
    elif ally_elements is not None:
        weak_list = [_elem_str(w) for w in ally_elements]
    else:
        weak_list = list(ALL_ELEMENTS)
    # Deduplicate, stable order by ALL_ELEMENTS then extras
    ordered: list[str] = []
    seen: set[str] = set()
    for e in list(ALL_ELEMENTS) + weak_list:
        if e in weak_list and e not in seen:
            seen.add(e)
            ordered.append(e)
    raw = {
        "id": DUMMY_ID,
        "name": "Lv95 训练人偶",
        "level": DUMMY_LEVEL,
        "path": "destruction",
        "elements": ["physical"],
        "weaknesses": ordered,
        "toughness_max": DUMMY_TOUGHNESS,
        "immortal": immortal,
        "build": {
            "stats": {
                "hp_max": DUMMY_HP,
                "atk": 0.0,
                "defense": DUMMY_DEFENSE,
                "speed": DUMMY_SPEED,
                "crit_rate": 0.0,
                "crit_dmg": 0.0,
                "break_effect": 0.0,
                "effect_res": DUMMY_EFFECT_RES,
                "dmg_boost": {},
                "res_pen": {},
                "element_res": element_res_table(ordered),
                "energy_max": 100.0,
                "elation": 0.0,
                "punchline": 0.0,
                "merrymake": 0.0,
            },
            "actions": [
                {
                    "id": "dummy_pass",
                    "name": "待机",
                    "type": "basic_attack",
                    "description": "Does nothing",
                    "damage_instances": [],
                    "energy_cost": 0.0,
                    "sp_cost": 0,
                    "applies_effects": [],
                    "variable_changes": {},
                    "requires": [],
                }
            ],
            "effects": [],
            "variables": [],
        },
    }
    return Character.model_validate(copy.deepcopy(raw))


def enemy_snapshot(enemy: Character) -> dict:
    """Comparable fields for L1/L2 structural equality tests."""
    return {
        "id": enemy.id,
        "level": int(enemy.level),
        "defense": float(enemy.build.stats.defense),
        "speed": float(enemy.build.stats.speed),
        "toughness_max": float(enemy.toughness_max),
        "effect_res": float(enemy.build.stats.effect_res),
        "weaknesses": sorted(_elem_str(w) for w in enemy.weaknesses),
        "element_res": {
            k: float(v) for k, v in sorted((enemy.build.stats.element_res or {}).items())
        },
        "hit_target_rule": DUMMY_HIT_TARGET_RULE,
    }


def fribbels_default_enemy_table() -> dict:
    """Config comparison: this repo vs Fribbels optimizerFormDefaults."""
    from hsrsim.enemies.path_base_aggro import AGGRO_SOURCE, PATH_BASE_AGGRO

    return {
        "source_fribbels": "hsr-optimizer-main/.../optimizerFormDefaults.ts",
        "fields": {
            "level": {"fribbels": 95, "ours": DUMMY_LEVEL},
            "enemy_count": {"fribbels": 1, "ours": 1},
            "non_weak_res": {"fribbels": 0.2, "ours": DEFAULT_ELEMENT_RES},
            "elemental_weak": {
                "fribbels": "true → RES=0 on hit element",
                "ours": "weaknesses=ally damage elements → RES=0",
            },
            "weakness_broken_default": {
                "fribbels": False,
                "ours": "scenario toughness_mode (Control A: realistic)",
            },
            "max_toughness": {"fribbels": 360, "ours": DUMMY_TOUGHNESS},
            "effect_res": {"fribbels": 0.3, "ours": DUMMY_EFFECT_RES},
            "defense_stat": {
                "fribbels": "unused (level formula)",
                "ours": f"nominal {DUMMY_DEFENSE}; DefZone uses Lv+20",
            },
            "speed": {
                "fribbels": "N/A (no enemy AV)",
                "ours": DUMMY_SPEED,
            },
            "hit_target_rule": {
                "fribbels": "N/A",
                "ours": DUMMY_HIT_TARGET_RULE,
                "assumption": (
                    f"每次敌方行动打 {DUMMY_HITS_PER_ENEMY_ACTION} 个我方目标；"
                    "加权随机=命途 BaseAggro；L1 取期望；无敌方伤害数值"
                ),
                "aggro_source": AGGRO_SOURCE,
                "path_base_aggro": dict(PATH_BASE_AGGRO),
            },
            "hits_per_enemy_action": {
                "fribbels": "N/A",
                "ours": DUMMY_HITS_PER_ENEMY_ACTION,
            },
            "weakness_res_pen": {
                "fribbels": "none",
                "ours": "removed  (no source)",
            },
        },
    }
