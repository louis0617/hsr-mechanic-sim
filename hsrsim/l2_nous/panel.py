"""Read effective panels from our Character and from HSR_Nous CombatEngine."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from hsrsim.l2_nous.compile import nous_template_root_strs
from hsrsim.l2_nous.paths import HSR_NOUS_ROOT
from hsrsim.simulator.types import Character

_SRC = str((HSR_NOUS_ROOT / "src").resolve())
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


def _elem_key(el) -> str:
    return el.value if hasattr(el, "value") else str(el)


def our_panel(char: Character) -> dict[str, float]:
    s = char.build.stats
    elem = _elem_key(char.elements[0]) if char.elements else ""
    dmg = 0.0
    for k, v in (s.dmg_boost or {}).items():
        ks = _elem_key(k)
        if elem and ks == elem:
            dmg = float(v or 0.0)
            break
        if ks == "all":
            dmg += float(v or 0.0)
    res_pen = 0.0
    for k, v in (s.res_pen or {}).items():
        ks = _elem_key(k)
        if elem and ks == elem:
            res_pen = float(v or 0.0)
            break
        if ks == "all":
            res_pen += float(v or 0.0)
    return {
        "atk": float(s.atk),
        "hp": float(s.hp_max),
        "def": float(s.defense),
        "spd": float(s.speed),
        "crit_rate": float(s.crit_rate),
        "crit_dmg": float(s.crit_dmg),
        "effect_hit": float(s.ehr),
        "effect_res": float(s.effect_res),
        "dmg_boost": dmg,
        "res_pen": res_pen,
        "def_pen": 0.0,
        "_element": elem,
    }


def _elem_aliases(element: str) -> tuple[str, ...]:
    el = (element or "").lower()
    if el in ("lightning", "thunder"):
        return ("lightning", "thunder")
    return (el,) if el else ()


def _nous_dmg_boost(eff: dict[str, Any], element: str) -> float:
    # effective_stats may expose dmg_thunder / dmg_bonus / etc.
    key_map = {
        "lightning": "dmg_thunder",
        "thunder": "dmg_thunder",
        "fire": "dmg_fire",
        "ice": "dmg_ice",
        "imaginary": "dmg_imaginary",
        "quantum": "dmg_quantum",
        "wind": "dmg_wind",
        "physical": "dmg_physical",
    }
    aliases = _elem_aliases(element)
    for el in aliases:
        k = key_map.get(el, f"dmg_{el}")
        if k in eff and eff[k] is not None:
            return float(eff[k] or 0.0)
    db = eff.get("dmg_bonus")
    if isinstance(db, dict):
        for el in aliases or (element.lower(),):
            if el in db:
                return float(db[el] or 0.0)
        # also accept all_dmg folded under 'all' if present on some builds
        if "all" in db:
            return float(db["all"] or 0.0)
    if isinstance(db, (int, float)):
        return float(db)
    # bare keys without dmg_ prefix
    alt = {
        "lightning": "thunder_dmg",
        "thunder": "thunder_dmg",
        "fire": "fire_dmg",
        "ice": "ice_dmg",
        "imaginary": "imaginary_dmg",
        "quantum": "quantum_dmg",
    }.get(element.lower())
    if alt and alt in eff:
        return float(eff[alt] or 0.0)
    return 0.0


# Battle-start / combat-conditional modifiers that inflate Nous effective_stats
# beyond our static loadout panel (equipment + permanent (a) passives).
_COMBAT_MOD_PREFIXES = (
    "SET_117_4PC_DOUBLE",
    "SET_117_4PC_CD",
    "SET_310_TEAM",  # keel team CD — combat aura; not on our static panel
    "ULT_RES_SHRED",
    "ABYSS_",
    "E2_ABYSS",
    "LC_23021_MASK",
    "SPK_NOCTURNE",  # Sparkle skill ATK% — combat buff
    "FORTIFIED_WAGER",
    "HOT_HAND",
    "WAGER_",
    "PELA_SECRET_STRATEGY",  # talent EHR channel; our static uses flat_traces only
)


def _strip_combat_modifiers(st: Any) -> list[str]:
    removed: list[str] = []
    for mid in list(st.modifiers.keys()):
        if mid.startswith(_COMBAT_MOD_PREFIXES) or any(
            mid.startswith(p) for p in _COMBAT_MOD_PREFIXES
        ):
            del st.modifiers[mid]
            removed.append(mid)
    return removed


def _flat_traces_to_stat_effects(
    flat_traces: dict[str, Any] | None, *, element_hint: str = ""
) -> dict[str, float]:
    """Map our ``_meta.flat_traces`` keys → Nous modifier stat_effects."""
    if not flat_traces:
        return {}
    out: dict[str, float] = {}
    dmg_aliases = {
        "lightning": "dmg_thunder",
        "thunder": "dmg_thunder",
        "fire": "dmg_fire",
        "ice": "dmg_ice",
        "imaginary": "dmg_imaginary",
        "quantum": "dmg_quantum",
        "wind": "dmg_wind",
        "physical": "dmg_physical",
    }
    for k, v in flat_traces.items():
        if v is None:
            continue
        key = str(k)
        if key.startswith("dmg_boost."):
            el = key.split(".", 1)[1]
            nk = dmg_aliases.get(el, f"dmg_{el}")
            out[nk] = float(v)
        elif key == "speed_flat":
            out["spd"] = out.get("spd", 0.0) + float(v)
        elif key in ("atk_pct", "hp_pct", "def_pct", "spd_pct", "crit_rate", "crit_dmg", "ehr", "effect_res"):
            out[key if key != "ehr" else "effect_hit"] = float(v)
        elif key in ("atk", "hp", "def", "spd"):
            out[key if key != "def" else "def_"] = float(v)
    # if traces only had generic and element_hint provided, nothing else to do
    _ = element_hint
    return out


def _select_trace_inject(
    candidates: dict[str, float],
    *,
    bare: dict[str, Any],
    ref_our: dict[str, float] | None,
    element_hint: str = "",
    tol: float = 1e-3,
) -> dict[str, float]:
    """Keep only trace keys that close a shortfall vs ``ref_our``.

    If ``ref_our`` is None, inject everything (legacy). Fixtures that already
    bake SPD/EHR into ``base_stats`` must not receive a second copy.
    """
    if not candidates:
        return {}
    if ref_our is None:
        return dict(candidates)

    bare_atk = float(bare.get("atk", 0.0) or 0.0)
    bare_hp = float(bare.get("hp", 0.0) or 0.0)
    bare_spd = float(bare.get("spd", 0.0) or 0.0)
    bare_cr = float(bare.get("crit_rate", 0.0) or 0.0)
    bare_cd = float(bare.get("crit_dmg", 0.0) or 0.0)
    bare_ehr = float(bare.get("effect_hit", 0.0) or 0.0)
    bare_dmg = _nous_dmg_boost(bare, element_hint) if element_hint else 0.0
    atk_pct_bare = float(bare.get("atk_pct", 0.0) or 0.0)
    white_atk = bare_atk / (1.0 + atk_pct_bare) if atk_pct_bare > 0 else bare_atk

    out: dict[str, float] = {}
    for k, v in candidates.items():
        need = False
        if k == "atk_pct":
            gap = float(ref_our.get("atk", 0.0) or 0.0) - bare_atk
            need = gap > tol and gap + tol >= white_atk * float(v) * 0.5
        elif k == "hp_pct":
            gap = float(ref_our.get("hp", 0.0) or 0.0) - bare_hp
            need = gap > tol
        elif k == "spd":
            gap = float(ref_our.get("spd", 0.0) or 0.0) - bare_spd
            need = gap > tol and abs(gap - float(v)) <= max(tol, 0.05)
        elif k == "crit_rate":
            gap = float(ref_our.get("crit_rate", 0.0) or 0.0) - bare_cr
            need = gap > tol and gap + tol >= float(v) * 0.5
        elif k == "crit_dmg":
            gap = float(ref_our.get("crit_dmg", 0.0) or 0.0) - bare_cd
            need = gap > tol and gap + tol >= float(v) * 0.5
        elif k == "effect_hit":
            gap = float(ref_our.get("effect_hit", 0.0) or 0.0) - bare_ehr
            need = gap > tol and gap + tol >= float(v) * 0.5
        elif k.startswith("dmg_"):
            gap = float(ref_our.get("dmg_boost", 0.0) or 0.0) - bare_dmg
            need = gap > tol and gap + tol >= float(v) * 0.5
        if need:
            out[k] = float(v)
    return out


def nous_panel_for_team(
    members: list[dict[str, Any]],
    actor_id: str,
    *,
    element_hint: str = "",
    equipment_only: bool = True,
    flat_traces: dict[str, Any] | None = None,
    ref_our: dict[str, float] | None = None,
) -> dict[str, float]:
    """Read Nous effective panel after setup.

    ``equipment_only=True`` (Phase 1 gate default): strip battle-start combat
    stacks / temporary LC auras and skip team combat auras (e.g. Sparkle
    Nocturne ATK%), so the compare matches our static loadout panel.

    ``flat_traces``: when Nous fixtures leave ``trace_stat_effects`` empty (common
    in vendored templates), selectively inject our ``_meta.flat_traces`` as a
    TRACE modifier for keys that still short the bare equipment panel vs
    ``ref_our``.
    """
    from hsr_nous.sim.compile import compile_encounter
    from hsr_nous.sim.engine import CombatEngine
    from hsr_nous.sim.pipeline import MODE_EXPECTED
    from hsr_nous.sim.state import Modifier

    build = {
        "build": {
            "team": members,
            "policy": {
                "name": "panel",
                "action_rules": [
                    {"condition": "true", "action": "basic", "priority": 0}
                ],
            },
        }
    }
    stage = {
        "stage": {
            "stage_id": "panel_dummy",
            "enemies": [
                {
                    "actor_id": "e1",
                    "name": "假人",
                    "hp": 1e9,
                    "spd": 90,
                    "atk": 1000,
                    "def": 1000,
                    "max_toughness": 360,
                    "weakness": ["thunder"],
                    # per-element resist; non-weakness baseline handled by rulebook
                    "resistance": {
                        "physical": 0.2,
                        "fire": 0.2,
                        "ice": 0.2,
                        "thunder": 0.0,
                        "wind": 0.2,
                        "quantum": 0.2,
                        "imaginary": 0.2,
                    },
                }
            ],
            "termination": {"mode": "fixed_av", "max_action_value": 150},
        }
    }
    compiled = compile_encounter(
        build, stage, template_roots=nous_template_root_strs()
    )
    eng = CombatEngine.from_compiled(
        compiled, mode=MODE_EXPECTED, initial_energy_ratio=0.0
    )
    eng.setup()
    # actor_id in state is usually template id e.g. "1308"
    st = eng.state.actors[actor_id]
    stripped: list[str] = []
    if equipment_only:
        stripped = _strip_combat_modifiers(st)

    # Some fixtures bake minor traces into base_stats / their own TRACE_* while
    # leaving other keys empty (e.g. 1308). Inject only keys that still short
    # the equipment panel versus a reference our-panel when provided; otherwise
    # inject the full flat_traces map.
    injected: dict[str, float] = {}
    candidates = _flat_traces_to_stat_effects(flat_traces, element_hint=element_hint)
    if candidates:
        bare = eng.pipeline.effective_stats(st, _skip_aura=bool(equipment_only))
        injected = _select_trace_inject(
            candidates,
            bare=bare,
            ref_our=ref_our,
            element_hint=element_hint,
        )
        if injected:
            st.modifiers[f"TRACE_OUR_{actor_id}"] = Modifier(
                modifier_id=f"TRACE_OUR_{actor_id}",
                name="行迹(我方flat_traces选择性注入)",
                modifier_type="buff",
                duration=0,
                dispellable=False,
                stat_effects=dict(injected),
                source_kind="trace",
            )
    eff = eng.pipeline.effective_stats(st, _skip_aura=bool(equipment_only))
    el = element_hint
    # permanent set all_dmg (e.g. pioneer 2pc) is hit-conditioned on Nous → not on
    # panel; our static panel also omits conditional 2pc. Compare elemental only.
    dmg = _nous_dmg_boost(eff, el) if el else 0.0
    return {
        "atk": float(eff.get("atk", 0.0) or 0.0),
        "hp": float(eff.get("hp", 0.0) or 0.0),
        "def": float(eff.get("def_", eff.get("def", 0.0)) or 0.0),
        "spd": float(eff.get("spd", 0.0) or 0.0),
        "crit_rate": float(eff.get("crit_rate", 0.0) or 0.0),
        "crit_dmg": float(eff.get("crit_dmg", 0.0) or 0.0),
        "effect_hit": float(eff.get("effect_hit", 0.0) or 0.0),
        "effect_res": float(eff.get("effect_res", 0.0) or 0.0),
        "dmg_boost": dmg,
        "res_pen": float(eff.get("res_pen", 0.0) or 0.0),
        "def_pen": float(eff.get("def_pen", 0.0) or 0.0),
        "_raw_keys": sorted(eff.keys()),
        "_stripped_mods": stripped,
        "_injected_traces": injected,
        "_equipment_only": equipment_only,
    }


COMPARE_KEYS = (
    "atk",
    "hp",
    "spd",
    "crit_rate",
    "crit_dmg",
    "dmg_boost",
    "res_pen",
    "effect_hit",
)


def compare_panels(
    our: dict[str, float], nous: dict[str, float], *, tol: float = 1e-3
) -> list[dict[str, Any]]:
    rows = []
    for k in COMPARE_KEYS:
        a = float(our.get(k, 0.0) or 0.0)
        b = float(nous.get(k, 0.0) or 0.0)
        ok = abs(a - b) <= tol or (
            max(abs(a), abs(b)) > 1e-9 and abs(a - b) / max(abs(a), abs(b)) <= 1e-4
        )
        # absolute tol for large stats
        if k in ("atk", "hp", "def", "spd") and abs(a - b) <= 0.5:
            ok = True
        rows.append({"stat": k, "our": a, "nous": b, "delta": a - b, "ok": ok})
    return rows
