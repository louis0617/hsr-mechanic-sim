"""Run a fixed-AV encounter on HSR_Nous with our loadout + CycleAxis."""
from __future__ import annotations

import sys
from typing import Any

from hsrsim.axis.schema import CycleAxis
from hsrsim.l2_nous.compile import nous_template_root_strs
from hsrsim.l2_nous.map_loadout import TEAM_CALIBER, nous_team_members
from hsrsim.l2_nous.paths import HSR_NOUS_ROOT
from hsrsim.l2_nous.policy import AxisNousDecision, ULT_AFTER_ACTION
from hsrsim.l2_nous.verify import (
    assert_member_dicts_match_caliber,
    assert_setup_eidolon_markers,
)

_SRC = str((HSR_NOUS_ROOT / "src").resolve())
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


def _team_key_for_axis(axis: CycleAxis) -> str:
    tid = axis.team_id
    if tid in TEAM_CALIBER:
        return tid
    # community uses acheron_old_pela_res
    if tid == "acheron_old_pela_res":
        return "acheron_old_pela"
    if tid.endswith("_res"):
        alt = tid[: -len("_res")]
        if alt in TEAM_CALIBER:
            return alt
    raise KeyError(f"no TEAM_CALIBER for team_id={tid!r}")


def run_nous_axis(
    axis: CycleAxis,
    *,
    max_av: float = 10000.0,
    eidolon_override: dict[str, int] | None = None,
    zone_weakness: str = "thunder",
    reuse_compile: bool = False,
) -> dict[str, Any]:
    """Compile + run Nous engine; return DPR (per 100 AV) and counters.

    ``max_av``: termination AV. Our L2 uses 100 cycles ≈ first 150 + 99*100 AV
    for speed-based turns; for cross-check we use the same AV budget as our
    engine's ``av_clock`` after a matching run when provided by the caller.
    Default 10000 AV ≈ 100 cycles at typical speeds.

    ``reuse_compile``: if True, use ``rotation_search.compile_cache`` (compile once
    per team/AV key). Default False preserves prior per-call compile behavior.
    """
    if reuse_compile:
        from hsrsim.rotation.compile_cache import (
            compile_once,
            run_with_compiled,
        )

        compiled = compile_once(
            axis,
            max_av=max_av,
            eidolon_override=eidolon_override,
            zone_weakness=zone_weakness,
            use_cache=True,
        )
        return run_with_compiled(
            axis, compiled, eidolon_override=eidolon_override
        )

    from hsr_nous.sim import engine as nous_engine_mod
    from hsr_nous.sim.compile import compile_encounter
    from hsr_nous.sim.engine import CombatEngine
    from hsr_nous.sim.pipeline import MODE_EXPECTED

    # Vendored default MAX_TURNS_SAFETY=200 truncates ~25 cycles; raise for 100-cycle AV.
    if getattr(nous_engine_mod, "MAX_TURNS_SAFETY", 200) < 5000:
        nous_engine_mod.MAX_TURNS_SAFETY = 5000

    tkey = _team_key_for_axis(axis)
    members = nous_team_members(tkey)
    if eidolon_override:
        from hsrsim.l2_nous.ids import AVATAR_ID

        for slug, eid in eidolon_override.items():
            aid = AVATAR_ID.get(slug)
            for m in members:
                if str(m.get("character_template")) == str(aid):
                    m["eidolon"] = int(eid)
    else:
        # Hard gate: compiled intent must match TEAM_CALIBER (fails before run).
        assert_member_dicts_match_caliber(members, tkey)

    build = {
        "build": {
            "team": members,
            "policy": {
                "name": "axis_injected",
                "mode": "rule_based",
                "ult_timing": ULT_AFTER_ACTION,
                "action_rules": [
                    {"condition": "true", "action": "basic", "priority": 0}
                ],
            },
        }
    }
    stage = {
        "stage": {
            "stage_id": "cross_dummy",
            "enemies": [
                {
                    "actor_id": "e1",
                    "name": "假人",
                    "hp": 1e12,
                    "spd": 90,
                    "atk": 1000,
                    "def": 1000,
                    "max_toughness": 360,
                    "weakness": [zone_weakness],
                    "resistance": {
                        "physical": 0.2,
                        "fire": 0.2,
                        "ice": 0.2,
                        "thunder": 0.0 if zone_weakness == "thunder" else 0.2,
                        "wind": 0.2,
                        "quantum": 0.2,
                        "imaginary": 0.2,
                    },
                }
            ],
            "termination": {"mode": "fixed_av", "max_action_value": float(max_av)},
        }
    }
    compiled = compile_encounter(
        build, stage, template_roots=nous_template_root_strs()
    )
    eng = CombatEngine.from_compiled(
        compiled, mode=MODE_EXPECTED, initial_energy_ratio=0.0
    )
    eng.decision = AxisNousDecision(axis=axis, ult_timing=ULT_AFTER_ACTION)
    eng.setup()
    eidolon_markers = []
    if not eidolon_override:
        eidolon_markers = assert_setup_eidolon_markers(eng, tkey)
    eng.run()

    total = float(getattr(eng.state, "total_damage", 0.0) or 0.0)
    by_actor = dict(getattr(eng.state, "damage_by_actor", {}) or {})
    av = float(getattr(eng.state, "clock", 0.0) or 0.0)
    if av <= 0:
        av = float(max_av)
    dpr = total * (100.0 / av) if av > 0 else 0.0
    truncated = bool(getattr(eng.state, "truncated", False))
    return {
        "feasible": not truncated,
        "truncated": truncated,
        "dpr": dpr,
        "av": av,
        "total_damage": total,
        "by_actor": by_actor,
        "by_action_type": {},
        "n_action": int(getattr(eng.state, "turn_count", 0) or 0),
        "n_ult": 0,
        "skill_points_end": int(getattr(eng.state, "skill_points", 0) or 0),
        "cycle_index": int(getattr(eng.state, "cycle_index", 0) or 0),
        "axis_id": axis.axis_id,
        "team_key": tkey,
        "eidolon_markers": eidolon_markers,
    }
