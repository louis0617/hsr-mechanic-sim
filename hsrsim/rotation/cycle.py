"""Cycle detection on our L2.

Sample at each ally turn start. The signature is the union of
``cycle_signature_fields`` declared beside the character templates.
A repeated signature closes a cycle. Long-run DPR is
cycle damage / cycle AV, scaled per 100 AV to match ``play_axis_l2``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from hsrsim.rotation.dims_loader import load_team_signature_fields

_KIND_ZH = {
    "basic_attack": "普攻",
    "skill": "战技",
    "ultimate": "终结技",
    "follow_up": "追击",
    "technique": "秘技",
}
_ACTOR_ZH = {
    "acheron": "黄泉",
    "sparkle": "花火",
    "jiaoqiu": "椒丘",
    "pela": "佩拉",
    "aventurine": "砂金",
    "mortenaxblade": "千冶·刃",
}


def _quantize(value: Any) -> Any:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        rounded = round(float(value), 4)
        if abs(rounded - round(rounded)) < 1e-6:
            return int(round(rounded))
        return rounded
    return value


def _casefold_hit(mapping: dict, key: str):
    if key in mapping:
        return mapping[key]
    want = key.casefold()
    for existing, value in mapping.items():
        if str(existing).casefold() == want:
            return value
    return None


def _effect_stacks_folded(unit, effect_id: str) -> int | None:
    want = effect_id.casefold()
    found = False
    stacks = 0
    for effect in getattr(unit, "active_effects", []) or []:
        if str(effect.id).casefold() == want:
            found = True
            stacks = int(effect.current_stacks)
            break
    return stacks if found else None


def _unit(state, slug: str):
    for unit in list(state.allies) + list(state.enemies):
        if unit.char.id == slug:
            return unit
    return None


def _read_field(state, spec: dict, *, actor_id: str) -> tuple[Any, bool]:
    """Return (value, resolved). Unresolved ids are not treated as zero."""
    kind = spec.get("kind")
    on = spec.get("on")
    effect_id = spec.get("id")
    character = spec.get("character")

    if kind == "skill_points" and on == "team":
        return _quantize(state.sp_team_pool), True

    if kind == "av_phase" and on == "self":
        return int(actor_id == character), True

    if kind == "energy" and on == "self":
        unit = _unit(state, character)
        if unit is None:
            return None, False
        return int(round(float(unit.energy_current))), True

    if kind == "resource" and on == "self":
        unit = _unit(state, character)
        if unit is None or not effect_id:
            return None, False
        hit = _casefold_hit(dict(unit.variables), str(effect_id))
        if hit is None:
            return None, False
        return _quantize(hit), True

    if kind == "modifier_stacks" and effect_id:
        if on == "self":
            unit = _unit(state, character)
            if unit is None:
                return None, False
            stacks = _effect_stacks_folded(unit, str(effect_id))
            if stacks is None:
                return None, False
            return stacks, True
        if on == "allies_named":
            unit = _unit(state, spec.get("name"))
            if unit is None:
                return None, False
            stacks = _effect_stacks_folded(unit, str(effect_id))
            if stacks is None:
                return None, False
            return stacks, True
        if on == "enemies_max":
            found = False
            best = 0
            for enemy in state.enemies:
                stacks = _effect_stacks_folded(enemy, str(effect_id))
                if stacks is None:
                    continue
                found = True
                best = max(best, stacks)
            if not found:
                return None, False
            return best, True
        if on == "enemies_any":
            found = False
            any_stacks = 0
            for enemy in state.enemies:
                stacks = _effect_stacks_folded(enemy, str(effect_id))
                if stacks is None:
                    continue
                found = True
                if stacks > 0:
                    any_stacks = 1
            if not found:
                return None, False
            return any_stacks, True

    if kind == "dot_remaining_turns" and effect_id and on == "enemies_max":
        # Effect.duration_turns is the live remaining turn count (state.py tick).
        found = False
        best = 0
        want = str(effect_id).casefold()
        for enemy in state.enemies:
            for effect in getattr(enemy, "active_effects", []) or []:
                if str(effect.id).casefold() != want:
                    continue
                if getattr(effect, "dot_instance", None) is None:
                    continue
                found = True
                best = max(best, int(effect.duration_turns))
        if not found:
            return None, False
        return best, True

    return None, False


def read_signature(state, fields: list[dict], *, actor_id: str) -> tuple[tuple, list[str]]:
    parts: list[tuple] = []
    unresolved: list[str] = []
    for spec in fields:
        value, ok = _read_field(state, spec, actor_id=actor_id)
        token = (
            spec.get("character"),
            spec.get("kind"),
            spec.get("id"),
            spec.get("on"),
            spec.get("name"),
        )
        parts.append((token, value))
        if not ok:
            unresolved.append(
                f"{spec.get('character')}:{spec.get('kind')}:{spec.get('id')}"
            )
    return tuple(parts), unresolved


@dataclass
class CycleSample:
    signature: tuple
    av: float
    damage: float
    actor_id: str


@dataclass
class CycleObserver:
    """Record ally turn-start signatures. First repeated signature closes a cycle."""

    fields: list[dict]
    anchor_id: str | None = None
    n_closures: int = 1
    samples: list[CycleSample] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    _seen: dict[tuple, int] = field(default_factory=dict)
    _miss: set[str] = field(default_factory=set)
    _hit: set[str] = field(default_factory=set)
    closed: dict | None = None
    closures: list[dict] = field(default_factory=list)
    actions: list[dict] = field(default_factory=list)
    _period_signature: tuple | None = None

    def bind(self, engine) -> None:
        engine.bus.on("turn_start", lambda payload: self.on_turn_start(engine, payload))

    def on_turn_start(self, engine, payload: dict) -> None:
        if len(self.closures) >= max(1, self.n_closures):
            return
        actor_id = str(payload.get("unit_id") or "")
        state = engine.state
        ally_ids = {unit.char.id for unit in state.allies}
        if actor_id not in ally_ids:
            return
        if self.anchor_id is not None and actor_id != self.anchor_id:
            return
        signature, unresolved = read_signature(state, self.fields, actor_id=actor_id)
        missing = set(unresolved)
        for spec in self.fields:
            key = f"{spec.get('character')}:{spec.get('kind')}:{spec.get('id')}"
            if key in missing:
                self._miss.add(key)
            else:
                self._hit.add(key)
        self.unresolved = sorted(self._miss - self._hit)
        sample = CycleSample(
            signature=signature,
            av=float(state.av_clock),
            damage=float(state.total_damage_dealt),
            actor_id=actor_id,
        )
        previous = self._seen.get(signature)
        if previous is not None:
            if self._period_signature is not None and signature != self._period_signature:
                self.samples.append(sample)
                return
            closed = _close(self.samples[previous], sample)
            closed["match_index"] = previous
            closed["turns_before_repeat"] = len(self.samples)
            self.closures.append(closed)
            self.closed = self.closures[0]
            if self._period_signature is None:
                self._period_signature = signature
            if len(self.closures) == 1:
                self.actions = actions_between(
                    getattr(engine, "events", []),
                    closed["av_start"],
                    closed["av_end"],
                )
            if len(self.closures) >= max(1, self.n_closures):
                return
            self.samples.append(sample)
            self._seen[signature] = len(self.samples) - 1
            return
        self._seen[signature] = len(self.samples)
        self.samples.append(sample)

    def result(self, engine=None) -> dict:
        hit = self.closed
        actions = list(self.actions)
        if not actions and hit is not None and engine is not None:
            actions = actions_between(engine.events, hit["av_start"], hit["av_end"])
        readable = format_cycle(actions) if hit is not None else ""
        if hit is None:
            return {
                "converged": False,
                "samples": len(self.samples),
                "unresolved_fields": list(self.unresolved),
                "cycle_dpr": None,
                "cycle_av": None,
                "cycle_damage": None,
                "actions": [],
                "readable": "",
            }
        return {
            "converged": True,
            "samples": len(self.samples),
            "unresolved_fields": list(self.unresolved),
            "cycle_dpr": hit["cycle_dpr"],
            "cycle_av": hit["cycle_av"],
            "cycle_damage": hit["cycle_damage"],
            "match_index": hit.get("match_index"),
            "turns_before_repeat": hit.get("turns_before_repeat"),
            "closures": [
                {
                    "cycle_av": row["cycle_av"],
                    "cycle_damage": row["cycle_damage"],
                    "cycle_dpr": row["cycle_dpr"],
                    "match_index": row.get("match_index"),
                    "turns_before_repeat": row.get("turns_before_repeat"),
                }
                for row in self.closures
            ],
            "actions": actions,
            "readable": readable,
        }


def _close(start: CycleSample, end: CycleSample) -> dict:
    av = end.av - start.av
    damage = end.damage - start.damage
    dpr = None if av <= 1e-9 else damage * (100.0 / av)
    return {
        "av_start": start.av,
        "av_end": end.av,
        "cycle_av": av,
        "cycle_damage": damage,
        "cycle_dpr": dpr,
    }


def actions_between(events, av_start: float, av_end: float) -> list[dict]:
    out: list[dict] = []
    for event in events:
        if getattr(event, "event_type", None) != "action_executed":
            continue
        payload = event.payload or {}
        av = float(payload.get("av_clock") or 0.0)
        if av_start < av <= av_end + 1e-6:
            out.append(
                {
                    "actor_id": payload.get("actor_id"),
                    "action_id": payload.get("action_id"),
                    "kind": payload.get("kind"),
                    "av_clock": av,
                }
            )
    return out


def _action_word(action: dict) -> str:
    aid = str(action.get("action_id") or "")
    kind = str(action.get("kind") or "")
    low = aid.lower()
    if low.endswith("_basic") or low.endswith("basic_attack"):
        return "普攻"
    if low.endswith("_ult") or low.endswith("_ultimate"):
        return "终结技"
    if "fua" in low or "follow" in low:
        return "追击"
    if low.endswith("_skill"):
        return "战技"
    if low == "dummy_pass" or low.endswith("_pass"):
        return "行动"
    return _KIND_ZH.get(kind, kind or aid)


def format_cycle(actions: list[dict]) -> str:
    parts = []
    for action in actions:
        actor_id = str(action.get("actor_id") or "")
        if actor_id in _ACTOR_ZH:
            actor = _ACTOR_ZH[actor_id]
        elif "dummy" in actor_id or actor_id.startswith("lv"):
            actor = "敌人"
        else:
            actor = actor_id
        parts.append(f"{actor}{_action_word(action)}")
    return " → ".join(parts)


def observe_axis(
    axis,
    *,
    zone: bool,
    max_cycles: int = 100,
    anchor_id: str = "acheron",
    n_closures: int = 1,
    **play_kwargs,
) -> dict:
    """Play one axis on L2 and attach cycle detection. Does not search."""
    from hsrsim.axis.eval import play_axis_l2

    fields = load_team_signature_fields(list(axis.members))
    observer = CycleObserver(fields, anchor_id=anchor_id, n_closures=n_closures)
    damage_log: list[tuple[float, float]] = []

    def _hook(engine) -> None:
        observer.bind(engine)

        def _on_hit(payload: dict) -> None:
            damage_log.append(
                (float(engine.state.av_clock), float(payload.get("damage") or 0.0))
            )

        engine.bus.on("damage_hit", _on_hit)

    played = play_axis_l2(
        axis,
        zone=zone,
        max_cycles=max_cycles,
        before_run=_hook,
        **play_kwargs,
    )
    played["cycle"] = observer.result(played.get("engine"))
    av_end = float(played.get("av") or 0.0)
    cutoff = 0.2 * av_end
    total = sum(damage for _, damage in damage_log)
    warmup = sum(damage for av, damage in damage_log if av < cutoff)
    span = av_end - cutoff
    played["steady_dpr"] = None if span <= 1e-9 else (total - warmup) * (100.0 / span)
    played["steady_warmup_av_frac"] = 0.2
    return played
