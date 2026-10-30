"""Coverage at hit: live-state snapshot (E1.1h-3 path) plus event-log path (F-a)."""
from __future__ import annotations

from collections import defaultdict

from hsrsim.simulator.engine import Engine
from hsrsim.simulator.triggers import LC_MIRAGE_ID, SPARKLE_FIGMENT_ID
from hsrsim.simulator.ult_timing import SPARKLE_SKILL_BUFF_ID

LC_MASK = "lc_mask"
CIPHER = "sparkle_enemy_vuln"


def _has(cs, eid: str) -> bool:
    if cs is None:
        return False
    return any(e.id == eid for e in cs.active_effects)


def attach_live_sampler(eng: Engine) -> dict[str, list[dict[str, float]]]:
    snap: dict[str, list[dict[str, float]]] = {"basic": [], "skill": [], "ult": []}
    orig = eng._resolve_damage

    def hooked(attacker, defender, dmg, action):
        if attacker and attacker.char.id == "acheron":
            kind = action.type.value if hasattr(action.type, "value") else str(action.type)
            sparkle = eng.state.find_char("sparkle")
            rec = {
                "sparkle_skill": float(_has(attacker, SPARKLE_SKILL_BUFF_ID)),
                "mask": float(_has(attacker, LC_MASK)),
                "mirage": float(bool(defender and _has(defender, LC_MIRAGE_ID))),
                "cipher": float(_has(attacker, CIPHER)),
                "figment_stacks": next(
                    (
                        float(e.current_stacks)
                        for e in (sparkle.active_effects if sparkle else [])
                        if e.id == SPARKLE_FIGMENT_ID
                    ),
                    0.0,
                ),
            }
            if kind == "ultimate" or action.id == "acheron_ult":
                snap["ult"].append(rec)
            elif kind == "skill" or action.id == "acheron_skill":
                snap["skill"].append(rec)
            elif kind == "basic_attack" or action.id == "acheron_basic":
                snap["basic"].append(rec)
        return orig(attacker, defender, dmg, action)

    eng._resolve_damage = hooked  # type: ignore[method-assign]
    return snap


def summarize_snaps(snap: dict[str, list[dict[str, float]]]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for act, rows in snap.items():
        if not rows:
            out[act] = {}
            continue
        keys = rows[0].keys()
        out[act] = {k: sum(float(r[k]) for r in rows) / len(rows) for k in keys}
        out[act]["n"] = float(len(rows))
    return out


def measure_event_log_sparkle(eng: Engine) -> dict[str, float]:
    """F-a: reconstruct sparkle skill buff from eng.events."""
    buff_on = False
    hits: dict[str, list[int]] = defaultdict(list)
    for ev in eng.events:
        eid = (ev.payload or {}).get("effect_id")
        tid = (ev.payload or {}).get("target_id")
        if ev.event_type == "effect_applied" and eid == SPARKLE_SKILL_BUFF_ID and tid == "acheron":
            buff_on = True
        elif ev.event_type == "effect_expired" and eid == SPARKLE_SKILL_BUFF_ID and tid == "acheron":
            buff_on = False
        elif ev.event_type == "damage":
            if (ev.payload or {}).get("attacker") != "acheron":
                continue
            aid = str((ev.payload or {}).get("action") or "")
            if "ult" in aid:
                key = "ult"
            elif "skill" in aid:
                key = "skill"
            elif "basic" in aid:
                key = "basic"
            else:
                continue
            hits[key].append(1 if buff_on else 0)
    out = {}
    for k in ("basic", "skill", "ult"):
        xs = hits.get(k) or []
        out[k] = (sum(xs) / len(xs)) if xs else 0.0
        out[f"n_{k}"] = float(len(xs))
    return out
