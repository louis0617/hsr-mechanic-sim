"""BenchmarkLoadout: Fribbels 200% perfection — objective-solver allocation (D9)."""
from __future__ import annotations

from dataclasses import dataclass, field
from math import ceil
from pathlib import Path
from typing import Any

import yaml

from hsrsim.loadout.static_bonuses import (
    IZUMO_SAME_PATH_CR,
    PanelBonus,
    light_cone_bonus,
    merge_bonuses,
    pan_cosmic_atk_flat,
    set_bonus_a,
)
from hsrsim.simulator.types import Character

REPO = Path(__file__).resolve().parents[2]
LOADOUT_DIR = REPO / "configs" / "loadout"

# 5★ +15 main (Fribbels MainStatsValues)
_MAIN = {
    "hp_pct": 6.912 + 15 * 2.4192,
    "atk_pct": 6.912 + 15 * 2.4192,
    "def_pct": 8.64 + 15 * 3.024,
    "hp_flat": 112.896 + 15 * 39.5136,
    "atk_flat": 56.448 + 15 * 19.7568,
    "speed": 4.032 + 15 * 1.4,
    "crit_rate": 5.184 + 15 * 1.8144,
    "crit_dmg": 10.368 + 15 * 3.6288,
    "ehr": 6.912 + 15 * 2.4192,
    "lightning_dmg": 6.2208 + 15 * 2.1773,
    "fire_dmg": 6.2208 + 15 * 2.1773,
    "ice_dmg": 6.2208 + 15 * 2.1773,
    "imaginary_dmg": 6.2208 + 15 * 2.1773,
    "quantum_dmg": 6.2208 + 15 * 2.1773,
    "err": 3.1104 + 15 * 1.0886,
}

_SUB = {
    "speed": 2.6,
    "crit_rate": 3.24,
    "crit_dmg": 6.48,
    "atk_pct": 4.32,
    "hp_pct": 4.32,
    "def_pct": 5.4,
    "ehr": 4.32,
    "atk_flat": 21.168754,
    "hp_flat": 42.33751,
    "def_flat": 21.168754,
    "effect_res": 4.32,
}

_PCT_STATS = frozenset(
    {
        "hp_pct",
        "atk_pct",
        "def_pct",
        "crit_rate",
        "crit_dmg",
        "ehr",
        "effect_res",
        "lightning_dmg",
        "fire_dmg",
        "ice_dmg",
        "imaginary_dmg",
        "quantum_dmg",
        "err",
    }
)

PIECES = ("head", "hands", "body", "feet", "sphere", "rope")
TOTAL_ROLLS = 54
LINES_PER_PIECE = 4
ROLLS_PER_PIECE = TOTAL_ROLLS // len(PIECES)

# Role → candidate substats for objective search (after SPD lock)
_ROLE_CANDIDATES: dict[str, list[str]] = {
    "main_dps": ["crit_rate", "crit_dmg", "atk_pct", "atk_flat"],
    "pull_support": ["crit_dmg", "hp_pct", "atk_pct"],
    "amp_support": ["ehr", "atk_pct", "atk_flat"],
    "shielder": ["def_pct", "def_flat", "hp_pct"],
}

# Broken Keel 310 (b): wearer RES ≥30% → team +10% CD. Generator must satisfy.
KEEL_PLANAR_ID = "broken_keel_310"
KEEL_RES_THRESHOLD = 0.30


@dataclass
class PieceBuild:
    main: str
    subs: dict[str, int] = field(default_factory=dict)


@dataclass
class CharacterLoadoutResult:
    character_id: str
    target_speed: float
    actual_speed: float
    spd_rolls: int
    rolls_by_stat: dict[str, int]
    pieces: dict[str, PieceBuild]
    panel_before: dict[str, Any]
    panel_after: dict[str, Any]
    crowded_out: dict[str, int]
    notes: list[str] = field(default_factory=list)
    objective_value: float | None = None
    marginal: dict[str, float] = field(default_factory=dict)


def _pct(v: float) -> float:
    return v / 100.0


def load_loadout_config(team_id: str) -> dict | None:
    path = LOADOUT_DIR / f"{team_id}.yaml"
    if not path.is_file():
        return None
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _forbidden_subs(main: str) -> set[str]:
    return {main}


def expected_damage_mult(atk: float, cr: float, cd: float) -> float:
    """ATK × (1 + min(1,CR)×CD) — Acheron objective."""
    return atk * (1.0 + min(1.0, cr) * cd)


# Jiaoqiu ult L10 ParamList[1] = 0.6 is the base chance (TextMap #2).
# Skill ParamList[2] and talent ParamList[0] are 1.0. GSD dummy effect_res is 0.
# Further EHR does not change hit once base*(1+EHR)*(1-RES) >= 1.
_AMP_HARDEST_BASE_CHANCE = 0.6
_GSD_DUMMY_EFFECT_RES = 0.0


def ehr_hit_saturation(base_chance: float, enemy_res: float) -> float:
    denom = float(base_chance) * (1.0 - float(enemy_res))
    if denom <= 1e-12:
        return 0.0
    return max(0.0, 1.0 / denom - 1.0)


@dataclass
class _PanelCtx:
    """Fixed bases + LC/set (a) for evaluating a candidate roll layout."""

    base_atk: float
    base_hp: float
    base_def: float
    base_spd: float
    base_cr: float
    base_cd: float
    base_ehr: float
    base_effect_res: float
    trace_atk_pct: float
    trace_hp_pct: float
    trace_def_pct: float
    bonus: PanelBonus
    mains: dict[str, str]
    role: str
    pan_cosmic: bool = False
    izumo_cr: bool = False
    # None = no EHR saturation. Amp support uses the hardest datamine base chance.
    ehr_hit_cap: float | None = None


def _sum_subs(pieces: dict[str, PieceBuild]) -> dict[str, float]:
    out: dict[str, float] = {}
    for pb in pieces.values():
        for stat, rolls in pb.subs.items():
            out[stat] = out.get(stat, 0.0) + rolls * _SUB[stat]
    return out


def _sum_mains(pieces: dict[str, PieceBuild]) -> dict[str, float]:
    out: dict[str, float] = {}
    for pb in pieces.values():
        out[pb.main] = out.get(pb.main, 0.0) + _MAIN[pb.main]
    return out


def _eval_panel(ctx: _PanelCtx, pieces: dict[str, PieceBuild]) -> dict[str, float]:
    main_sums = _sum_mains(pieces)
    sub_sums = _sum_subs(pieces)
    b = ctx.bonus

    def take_pct(*keys: str) -> float:
        return sum(_pct(main_sums.get(k, 0.0) + sub_sums.get(k, 0.0)) for k in keys)

    def take_flat(*keys: str) -> float:
        return sum(main_sums.get(k, 0.0) + sub_sums.get(k, 0.0) for k in keys)

    atk_pct = ctx.trace_atk_pct + b.atk_pct + take_pct("atk_pct")
    hp_pct = ctx.trace_hp_pct + b.hp_pct + take_pct("hp_pct")
    def_pct = ctx.trace_def_pct + b.def_pct + take_pct("def_pct")
    atk_flat = take_flat("atk_flat")
    hp_flat = take_flat("hp_flat")
    def_flat = take_flat("def_flat")

    atk = ctx.base_atk * (1.0 + atk_pct) + atk_flat
    ehr = ctx.base_ehr + b.ehr + take_pct("ehr")
    if ctx.pan_cosmic:
        atk += pan_cosmic_atk_flat(ctx.base_atk, ehr)

    cr = ctx.base_cr + b.crit_rate + take_pct("crit_rate")
    if ctx.izumo_cr:
        cr += IZUMO_SAME_PATH_CR
    cr = min(1.0, cr)
    cd = ctx.base_cd + b.crit_dmg + take_pct("crit_dmg")
    spd = ctx.base_spd * (1.0 + b.spd_pct) + take_flat("speed")
    defense = ctx.base_def * (1.0 + def_pct) + def_flat
    hp = ctx.base_hp * (1.0 + hp_pct) + hp_flat
    effect_res = ctx.base_effect_res + b.effect_res + take_pct("effect_res")

    return {
        "atk": atk,
        "hp_max": hp,
        "defense": defense,
        "speed": spd,
        "crit_rate": cr,
        "crit_dmg": cd,
        "ehr": ehr,
        "effect_res": effect_res,
    }


def _objective(role: str, panel: dict[str, float], *, ehr_hit_cap: float | None = None) -> float:
    if role == "main_dps":
        return expected_damage_mult(panel["atk"], panel["crit_rate"], panel["crit_dmg"])
    if role == "pull_support":
        return panel["crit_dmg"]
    if role == "amp_support":
        cap = ehr_hit_cap
        if cap is None or panel["ehr"] + 1e-12 < cap:
            return panel["ehr"] * 1000.0 + panel["atk"] * 1e-6
        # Hit chance already 1: extra EHR has zero marginal. Maximize ATK.
        return cap * 1000.0 + panel["atk"]
    if role == "shielder":
        return panel["defense"]
    raise ValueError(f"unknown role {role}")


def _add_roll(pieces: dict[str, PieceBuild], rolls_on_piece: dict[str, int], p: str, stat: str) -> bool:
    pb = pieces[p]
    if rolls_on_piece[p] >= ROLLS_PER_PIECE:
        return False
    if stat in _forbidden_subs(pb.main):
        return False
    if stat not in pb.subs and len(pb.subs) >= LINES_PER_PIECE:
        return False
    pb.subs[stat] = pb.subs.get(stat, 0) + 1
    rolls_on_piece[p] += 1
    return True


def _remove_roll(pieces: dict[str, PieceBuild], rolls_on_piece: dict[str, int], p: str, stat: str) -> None:
    pb = pieces[p]
    pb.subs[stat] -= 1
    if pb.subs[stat] <= 0:
        del pb.subs[stat]
    rolls_on_piece[p] -= 1


def _place_spd(
    pieces: dict[str, PieceBuild],
    rolls_on_piece: dict[str, int],
    mains: dict[str, str],
    spd_rolls: int,
) -> tuple[int, list[str]]:
    notes: list[str] = []
    spd_slots = [p for p in PIECES if p != "feet" and mains[p] != "speed"]
    max_cap = ROLLS_PER_PIECE * len(spd_slots)
    if spd_rolls > max_cap:
        notes.append(f"WARNING: spd_rolls {spd_rolls} capped to {max_cap}")
        spd_rolls = max_cap
    placed = 0
    guard = 0
    while placed < spd_rolls and guard < 1000:
        guard += 1
        progressed = False
        for p in spd_slots:
            if placed >= spd_rolls:
                break
            if _add_roll(pieces, rolls_on_piece, p, "speed"):
                placed += 1
                progressed = True
        if not progressed:
            break
    if placed < spd_rolls:
        notes.append(f"WARNING: only placed {placed}/{spd_rolls} SPD rolls")
    return placed, notes


def _candidate_placements(
    pieces: dict[str, PieceBuild],
    rolls_on_piece: dict[str, int],
    stat: str,
) -> list[str]:
    holders = [
        p
        for p in PIECES
        if stat in pieces[p].subs and rolls_on_piece[p] < ROLLS_PER_PIECE
    ]
    openable = [
        p
        for p in PIECES
        if rolls_on_piece[p] < ROLLS_PER_PIECE
        and stat not in _forbidden_subs(pieces[p].main)
        and (stat in pieces[p].subs or len(pieces[p].subs) < LINES_PER_PIECE)
    ]
    # Prefer spreading: piece with fewest rolls of this stat, then lowest total rolls
    out: list[str] = []
    for pool in (holders, openable):
        ranked = sorted(
            pool,
            key=lambda x: (pieces[x].subs.get(stat, 0), rolls_on_piece[x], PIECES.index(x)),
        )
        for p in ranked:
            if p not in out:
                out.append(p)
    return out


def _allocate_objective(
    *,
    mains: dict[str, str],
    target_speed: float,
    ctx: _PanelCtx,
) -> tuple[dict[str, PieceBuild], int, dict[str, int], dict[str, int], list[str], float, dict[str, float]]:
    pieces = {p: PieceBuild(main=mains[p]) for p in PIECES}
    rolls_on_piece = {p: 0 for p in PIECES}
    notes: list[str] = []

    # Empty layout to measure SPD before subs
    empty_panel = _eval_panel(ctx, pieces)
    # feet main speed is included via mains in empty pieces
    spd_before_subs = empty_panel["speed"]
    need = target_speed - spd_before_subs
    spd_rolls = max(0, ceil(need / _SUB["speed"] - 1e-9)) if need > 1e-9 else 0
    placed_spd, spd_notes = _place_spd(pieces, rolls_on_piece, mains, spd_rolls)
    notes.extend(spd_notes)
    spd_rolls = placed_spd

    candidates = list(_ROLE_CANDIDATES[ctx.role])
    # Greedy: repeatedly place the roll with best objective delta
    guard = 0
    while guard < 500:
        guard += 1
        if all(rolls_on_piece[p] >= ROLLS_PER_PIECE for p in PIECES):
            break
        best: tuple[float, str, str] | None = None  # (obj, stat, piece)
        for stat in candidates:
            for p in _candidate_placements(pieces, rolls_on_piece, stat):
                if not _add_roll(pieces, rolls_on_piece, p, stat):
                    continue
                obj = _objective(ctx.role, _eval_panel(ctx, pieces), ehr_hit_cap=ctx.ehr_hit_cap)
                _remove_roll(pieces, rolls_on_piece, p, stat)
                key = (obj, -candidates.index(stat), -PIECES.index(p))
                if best is None or key > (
                    best[0],
                    -candidates.index(best[1]),
                    -PIECES.index(best[2]),
                ):
                    best = (obj, stat, p)
        if best is None:
            break
        _add_roll(pieces, rolls_on_piece, best[2], best[1])

    unallocated = sum(ROLLS_PER_PIECE - rolls_on_piece[p] for p in PIECES)
    if unallocated > 0:
        notes.append(f"WARNING: {unallocated} rolls unallocated")

    # Local improvement: single-roll swaps among candidate stats
    improved = True
    swap_guard = 0
    while improved and swap_guard < 200:
        improved = False
        swap_guard += 1
        base_obj = _objective(ctx.role, _eval_panel(ctx, pieces), ehr_hit_cap=ctx.ehr_hit_cap)
        best_swap: tuple[float, str, str, str, str] | None = None
        for p_from in PIECES:
            for stat_from, n in list(pieces[p_from].subs.items()):
                if stat_from == "speed" or n <= 0:
                    continue
                if stat_from not in candidates:
                    continue
                _remove_roll(pieces, rolls_on_piece, p_from, stat_from)
                for stat_to in candidates:
                    if stat_to == stat_from:
                        continue
                    for p_to in _candidate_placements(pieces, rolls_on_piece, stat_to):
                        if not _add_roll(pieces, rolls_on_piece, p_to, stat_to):
                            continue
                        obj = _objective(ctx.role, _eval_panel(ctx, pieces), ehr_hit_cap=ctx.ehr_hit_cap)
                        _remove_roll(pieces, rolls_on_piece, p_to, stat_to)
                        if obj > base_obj + 1e-9:
                            if best_swap is None or obj > best_swap[0]:
                                best_swap = (obj, p_from, stat_from, p_to, stat_to)
                _add_roll(pieces, rolls_on_piece, p_from, stat_from)
        if best_swap is not None:
            _remove_roll(pieces, rolls_on_piece, best_swap[1], best_swap[2])
            if not _add_roll(pieces, rolls_on_piece, best_swap[3], best_swap[4]):
                _add_roll(pieces, rolls_on_piece, best_swap[1], best_swap[2])
            else:
                improved = True
                notes.append(
                    f"swap improved: {best_swap[2]}@{best_swap[1]} → {best_swap[4]}@{best_swap[3]}"
                )

    rolls_by_stat: dict[str, int] = {}
    for pb in pieces.values():
        for s, n in pb.subs.items():
            rolls_by_stat[s] = rolls_by_stat.get(s, 0) + n

    top_off = candidates[0] if candidates else "crit_dmg"
    crowded_out = {top_off: spd_rolls} if spd_rolls else {}

    final_panel = _eval_panel(ctx, pieces)
    obj_val = _objective(ctx.role, final_panel, ehr_hit_cap=ctx.ehr_hit_cap)

    # Marginal: +1 CR vs +1 CD (extra capacity), and swap one CD↔CR
    marginal: dict[str, float] = {}
    if ctx.role == "main_dps":
        for label, stat in (("plus_1_cr", "crit_rate"), ("plus_1_cd", "crit_dmg")):
            placed = False
            for p in _candidate_placements(pieces, rolls_on_piece, stat):
                if _add_roll(pieces, rolls_on_piece, p, stat):
                    marginal[label] = _objective(ctx.role, _eval_panel(ctx, pieces), ehr_hit_cap=ctx.ehr_hit_cap) - obj_val
                    _remove_roll(pieces, rolls_on_piece, p, stat)
                    placed = True
                    break
            if not placed:
                atk, cr, cd = final_panel["atk"], final_panel["crit_rate"], final_panel["crit_dmg"]
                if stat == "crit_rate":
                    cr2 = min(1.0, cr + _pct(_SUB["crit_rate"]))
                    marginal[label] = expected_damage_mult(atk, cr2, cd) - obj_val
                else:
                    cd2 = cd + _pct(_SUB["crit_dmg"])
                    marginal[label] = expected_damage_mult(atk, cr, cd2) - obj_val
                notes.append(f"marginal {label}: no free slot — formula delta without re-pack")
        # Swap diagnostics
        atk, cr, cd = final_panel["atk"], final_panel["crit_rate"], final_panel["crit_dmg"]
        cr_to_cd = expected_damage_mult(
            atk, max(0.0, cr - _pct(_SUB["crit_rate"])), cd + _pct(_SUB["crit_dmg"])
        ) - obj_val
        cd_to_cr = expected_damage_mult(
            atk, min(1.0, cr + _pct(_SUB["crit_rate"])), max(0.0, cd - _pct(_SUB["crit_dmg"]))
        ) - obj_val
        marginal["swap_cr_to_cd"] = cr_to_cd
        marginal["swap_cd_to_cr"] = cd_to_cr

    for p, pb in pieces.items():
        if len(pb.subs) > LINES_PER_PIECE:
            raise RuntimeError(f"{p} has {len(pb.subs)} sub lines")
        if pb.main in pb.subs:
            raise RuntimeError(f"{p} main {pb.main} duplicated as sub")
        if rolls_on_piece[p] > ROLLS_PER_PIECE:
            raise RuntimeError(f"{p} has {rolls_on_piece[p]} rolls")

    return pieces, spd_rolls, rolls_by_stat, crowded_out, notes, obj_val, marginal


def _ensure_keel_res_threshold(
    pieces: dict[str, PieceBuild],
    *,
    mains: dict[str, str],
    ctx: _PanelCtx,
) -> tuple[dict[str, PieceBuild], dict[str, int], list[str]]:
    """When wearing Broken Keel, force effect_res subs until panel RES ≥30%.

    Set (a) already adds +10% RES; base traces often leave ~20%. Without this,
    (b) team CD never fires — generator must satisfy set conditions.
    """
    notes: list[str] = []
    rolls_on_piece = {
        p: sum(pieces[p].subs.values()) for p in PIECES
    }
    panel = _eval_panel(ctx, pieces)
    if panel["effect_res"] + 1e-9 >= KEEL_RES_THRESHOLD:
        notes.append(
            f"Broken Keel RES ok: {panel['effect_res']:.4f}≥{KEEL_RES_THRESHOLD}"
        )
        rolls_by_stat: dict[str, int] = {}
        for pb in pieces.values():
            for s, n in pb.subs.items():
                rolls_by_stat[s] = rolls_by_stat.get(s, 0) + n
        return pieces, rolls_by_stat, notes

    # Prefer converting non-speed objective rolls → effect_res; else open new lines.
    need = KEEL_RES_THRESHOLD - panel["effect_res"]
    rolls_needed = max(1, ceil(need / _pct(_SUB["effect_res"]) - 1e-9))
    placed = 0
    # Phase 1: swap away from role candidates (except speed).
    candidates = list(_ROLE_CANDIDATES.get(ctx.role, []))
    guard = 0
    while placed < rolls_needed and guard < 200:
        guard += 1
        if panel["effect_res"] + 1e-9 >= KEEL_RES_THRESHOLD:
            break
        # Try open/add effect_res first if free capacity.
        added = False
        for p in _candidate_placements(pieces, rolls_on_piece, "effect_res"):
            if _add_roll(pieces, rolls_on_piece, p, "effect_res"):
                placed += 1
                added = True
                break
        if added:
            panel = _eval_panel(ctx, pieces)
            continue
        # Steal one roll from a candidate stat.
        stolen = False
        for p in PIECES:
            for stat in list(pieces[p].subs.keys()):
                if stat in ("speed", "effect_res"):
                    continue
                if stat not in candidates:
                    continue
                _remove_roll(pieces, rolls_on_piece, p, stat)
                if _add_roll(pieces, rolls_on_piece, p, "effect_res"):
                    placed += 1
                    stolen = True
                    break
                _add_roll(pieces, rolls_on_piece, p, stat)
            if stolen:
                break
        if not stolen:
            break
        panel = _eval_panel(ctx, pieces)

    panel = _eval_panel(ctx, pieces)
    if panel["effect_res"] + 1e-9 >= KEEL_RES_THRESHOLD:
        notes.append(
            f"Broken Keel: placed {placed} effect_res rolls → "
            f"RES={panel['effect_res']:.4f}≥{KEEL_RES_THRESHOLD} (set condition)"
        )
    else:
        notes.append(
            f"WARNING: Broken Keel RES still {panel['effect_res']:.4f}"
            f"<{KEEL_RES_THRESHOLD} after {placed} effect_res rolls"
        )
    rolls_by_stat = {}
    for pb in pieces.values():
        for s, n in pb.subs.items():
            rolls_by_stat[s] = rolls_by_stat.get(s, 0) + n
    return pieces, rolls_by_stat, notes


def _bonuses_for_character(
    cfg: dict,
    *,
    character_id: str,
    team_paths: dict[str, str] | None,
) -> tuple[PanelBonus, bool, bool]:
    """Return merged (a) bonus, pan_cosmic flag, izumo_cr flag."""
    parts: list[PanelBonus] = []
    lc = cfg.get("light_cone") or {}
    if lc:
        parts.append(light_cone_bonus(int(lc["id"]), int(lc.get("superimposition", 1))))
    sets = cfg.get("relic_sets") or {}
    pan = False
    izumo = False
    if sets:
        cavern = sets["cavern"]
        planar = sets["planar"]
        parts.append(set_bonus_a(cavern, planar))
        pan = planar == "pan_cosmic_303"
        if planar == "izumo_314" or cavern == "izumo_314":
            # planar only
            pass
        if planar == "izumo_314" and team_paths:
            my_path = team_paths.get(character_id)
            if my_path and any(p == my_path and cid != character_id for cid, p in team_paths.items()):
                izumo = True
    if not parts:
        return PanelBonus(), pan, izumo
    return merge_bonuses(*parts), pan, izumo


def apply_benchmark_to_character(
    char: Character,
    *,
    character_id: str,
    cfg: dict,
    raw_meta: dict | None = None,
    team_paths: dict[str, str] | None = None,
) -> tuple[Character, CharacterLoadoutResult]:
    char = char.model_copy(deep=True)
    target_speed = float(cfg["target_speed"])
    mains = dict(cfg["mains"])
    role = str(cfg.get("role") or "main_dps")

    before = {
        "hp_max": char.build.stats.hp_max,
        "atk": char.build.stats.atk,
        "defense": char.build.stats.defense,
        "speed": char.build.stats.speed,
        "crit_rate": char.build.stats.crit_rate,
        "crit_dmg": char.build.stats.crit_dmg,
        "ehr": char.build.stats.ehr,
        "effect_res": char.build.stats.effect_res,
        "err": char.build.stats.err,
        "dmg_boost": dict(char.build.stats.dmg_boost),
    }

    promo = (raw_meta or {}).get("promotion_base") or {}
    traces = (raw_meta or {}).get("flat_traces") or {}
    base_atk = float(promo.get("atk", char.build.stats.atk / (1.0 + float(traces.get("atk_pct", 0.0) or 0.0))))
    base_hp = float(promo.get("hp", char.build.stats.hp_max / (1.0 + float(traces.get("hp_pct", 0.0) or 0.0))))
    base_def = float(
        promo.get("defense", char.build.stats.defense / (1.0 + float(traces.get("def_pct", 0.0) or 0.0)))
    )
    base_spd = float(promo.get("speed", char.build.stats.speed)) + float(traces.get("speed_flat", 0.0) or 0.0)
    base_cr = float(promo.get("crit_rate", 0.05)) + float(traces.get("crit_rate", 0.0) or 0.0)
    base_cd = float(promo.get("crit_dmg", 0.5)) + float(traces.get("crit_dmg", 0.0) or 0.0)
    base_ehr = float(char.build.stats.ehr)
    if base_ehr < 1e-12 and traces.get("ehr") is not None:
        base_ehr = float(traces["ehr"])
    base_effect_res = float(char.build.stats.effect_res)
    if base_effect_res < 1e-12 and traces.get("effect_res") is not None:
        base_effect_res = float(traces["effect_res"])

    trace_atk_pct = float(traces.get("atk_pct", 0.0) or 0.0)
    trace_hp_pct = float(traces.get("hp_pct", 0.0) or 0.0)
    trace_def_pct = float(traces.get("def_pct", 0.0) or 0.0)

    bonus, pan_cosmic, izumo_cr = _bonuses_for_character(
        cfg, character_id=character_id, team_paths=team_paths
    )
    # LC bases fold into promotion bases
    base_atk = base_atk + bonus.atk_base
    base_hp = base_hp + bonus.hp_base
    base_def = base_def + bonus.def_base
    # Zero out bases already merged so _eval_panel does not double-add via bonus.*_base
    bonus_for_eval = PanelBonus(
        atk_pct=bonus.atk_pct,
        hp_pct=bonus.hp_pct,
        def_pct=bonus.def_pct,
        spd_pct=bonus.spd_pct,
        crit_rate=bonus.crit_rate,
        crit_dmg=bonus.crit_dmg,
        ehr=bonus.ehr,
        effect_res=bonus.effect_res,
        notes=list(bonus.notes),
    )

    ctx = _PanelCtx(
        base_atk=base_atk,
        base_hp=base_hp,
        base_def=base_def,
        base_spd=base_spd,
        base_cr=base_cr,
        base_cd=base_cd,
        base_ehr=base_ehr,
        base_effect_res=base_effect_res,
        trace_atk_pct=trace_atk_pct,
        trace_hp_pct=trace_hp_pct,
        trace_def_pct=trace_def_pct,
        bonus=bonus_for_eval,
        mains=mains,
        role=role,
        pan_cosmic=pan_cosmic,
        izumo_cr=izumo_cr,
        ehr_hit_cap=(
            ehr_hit_saturation(_AMP_HARDEST_BASE_CHANCE, _GSD_DUMMY_EFFECT_RES)
            if role == "amp_support"
            else None
        ),
    )

    pieces, spd_rolls, rolls_by_stat, crowded_out, notes, obj_val, marginal = _allocate_objective(
        mains=mains,
        target_speed=target_speed,
        ctx=ctx,
    )
    planar = str((cfg.get("relic_sets") or {}).get("planar") or "")
    if planar == KEEL_PLANAR_ID:
        pieces, rolls_by_stat, keel_notes = _ensure_keel_res_threshold(
            pieces, mains=mains, ctx=ctx
        )
        notes.extend(keel_notes)
        panel_keel = _eval_panel(ctx, pieces)
        obj_val = _objective(ctx.role, panel_keel, ehr_hit_cap=ctx.ehr_hit_cap)
    notes.extend(bonus.notes)
    if izumo_cr:
        notes.append("Izumo same-path +12% CR applied (team nihility)")
    if pan_cosmic:
        notes.append("Pan-Cosmic ATK-from-EHR applied after EHR")
    if role == "amp_support" and ctx.ehr_hit_cap is not None:
        ehr_subs = sum(pb.subs.get("ehr", 0) for pb in pieces.values())
        notes.append(
            f"EHR hit cap {ctx.ehr_hit_cap:.4f} "
            f"(ult base {_AMP_HARDEST_BASE_CHANCE} vs dummy RES {_GSD_DUMMY_EFFECT_RES}); "
            f"ehr sub rolls={ehr_subs}"
        )

    panel = _eval_panel(ctx, pieces)
    main_sums = _sum_mains(pieces)
    sub_sums = _sum_subs(pieces)

    char.build.stats.atk = panel["atk"]
    char.build.stats.hp_max = panel["hp_max"]
    char.build.stats.defense = panel["defense"]
    char.build.stats.speed = panel["speed"]
    char.build.stats.crit_rate = panel["crit_rate"]
    char.build.stats.crit_dmg = panel["crit_dmg"]
    char.build.stats.ehr = panel["ehr"]
    char.build.stats.effect_res = panel["effect_res"]

    for elem_key, elem in (
        ("lightning_dmg", "lightning"),
        ("fire_dmg", "fire"),
        ("ice_dmg", "ice"),
        ("imaginary_dmg", "imaginary"),
        ("quantum_dmg", "quantum"),
    ):
        add = _pct(main_sums.get(elem_key, 0.0) + sub_sums.get(elem_key, 0.0))
        if add or any(tk == f"dmg_boost.{elem}" for tk in traces):
            trace_elem = float(traces.get(f"dmg_boost.{elem}", 0.0) or 0.0)
            char.build.stats.dmg_boost[elem] = trace_elem + add

    if "err" not in cfg:
        raise ValueError(
            f"{character_id} loadout missing explicit err; refusing to default"
        )
    char.build.stats.err = float(cfg["err"])
    if "err" in main_sums:
        notes.append(
            "ERR rope main present; Stats.err uses loadout explicit err field only "
            f"(err={char.build.stats.err})"
        )
    else:
        notes.append(f"Stats.err set from loadout err={char.build.stats.err}")

    if character_id == "sparkle":
        for eff in char.build.effects:
            if eff.id != "sparkle_skill_buff":
                continue
            buff = 0.24 * char.build.stats.crit_dmg + 0.45
            for mod in eff.modifiers:
                if mod.target_stat == "crit_dmg":
                    mod.value = round(buff, 6)
            notes.append(f"sparkle_skill_buff recomputed to {buff:.4f} from panel CD")

    #stamp LC id / SI onto Character for wiring + runtime conditionals.
    lc_cfg = cfg.get("light_cone") or {}
    if lc_cfg:
        char.light_cone_id = int(lc_cfg["id"])
        char.light_cone_superimposition = int(lc_cfg.get("superimposition", 1))

    after = {
        "hp_max": char.build.stats.hp_max,
        "atk": char.build.stats.atk,
        "defense": char.build.stats.defense,
        "speed": char.build.stats.speed,
        "crit_rate": char.build.stats.crit_rate,
        "crit_dmg": char.build.stats.crit_dmg,
        "ehr": char.build.stats.ehr,
        "effect_res": char.build.stats.effect_res,
        "err": char.build.stats.err,
        "dmg_boost": dict(char.build.stats.dmg_boost),
    }

    result = CharacterLoadoutResult(
        character_id=character_id,
        target_speed=target_speed,
        actual_speed=float(char.build.stats.speed),
        spd_rolls=spd_rolls,
        rolls_by_stat=rolls_by_stat,
        pieces=pieces,
        panel_before=before,
        panel_after=after,
        crowded_out=crowded_out,
        notes=notes,
        objective_value=obj_val,
        marginal=marginal,
    )
    return char, result


def apply_team_benchmark(
    allies: list[Character],
    team_id: str,
    *,
    raw_metas: dict[str, dict] | None = None,
    speed_override: dict[str, float] | None = None,
) -> tuple[list[Character], list[CharacterLoadoutResult]]:
    cfg = load_loadout_config(team_id)
    if cfg is None:
        raise FileNotFoundError(f"no loadout config for {team_id}")
    team_paths = {c.id: c.path.value if hasattr(c.path, "value") else str(c.path) for c in allies}
    out_chars: list[Character] = []
    reports: list[CharacterLoadoutResult] = []
    by_id = {c.id: c for c in allies}
    for cid, ccfg in cfg["characters"].items():
        ccfg = dict(ccfg)
        if speed_override and cid in speed_override:
            ccfg["target_speed"] = speed_override[cid]
        char, rep = apply_benchmark_to_character(
            by_id[cid],
            character_id=cid,
            cfg=ccfg,
            raw_meta=(raw_metas or {}).get(cid),
            team_paths=team_paths,
        )
        out_chars.append(char)
        reports.append(rep)
    order = {c.id: i for i, c in enumerate(allies)}
    out_chars.sort(key=lambda c: order[c.id])
    return out_chars, reports
