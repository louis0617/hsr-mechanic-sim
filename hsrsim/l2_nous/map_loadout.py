"""Map our BenchmarkLoadout pieces → HSR_Nous member.relics (roll counts)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hsrsim.axis.community import (
    GUIDE_SPEED_JQ,
    GUIDE_SPEED_MORT,
    GUIDE_SPEED_PELA,
)
from hsrsim.l2_nous.ids import AFFIX, AVATAR_ID, LC_ID, set_slug_to_nous_id
from hsrsim.loadout.benchmark import (
    CharacterLoadoutResult,
    apply_benchmark_to_character,
    load_loadout_config,
)
from hsrsim.graph.loader import load_character_by_id
from hsrsim.simulator.types import Character

REPO = Path(__file__).resolve().parents[2]

# Caliber (Siqi 2026-10-11): 4★ char E6 + 4★ LC S5; 5★ per line.
# Acheron line: Acheron E2, 5★ supports E0, sig LC S1; Pela E6 + 21015 S5.
TEAM_CALIBER: dict[str, dict[str, Any]] = {
    "acheron_direct": {
        "loadout": "acheron_direct",
        "speed": GUIDE_SPEED_JQ,
        "eidolons": {"acheron": 2, "jiaoqiu": 0, "sparkle": 0, "aventurine": 0},
        "members": ["acheron", "jiaoqiu", "sparkle", "aventurine"],
    },
    "acheron_old_pela": {
        "loadout": "acheron_old_pela_res",
        "speed": GUIDE_SPEED_PELA,
        "eidolons": {"acheron": 2, "pela": 6, "sparkle": 0, "aventurine": 0},
        "members": ["acheron", "pela", "sparkle", "aventurine"],
    },
    "acheron_mortenax": {
        "loadout": "acheron_mortenax",
        "speed": GUIDE_SPEED_MORT,
        "eidolons": {"acheron": 2, "mortenaxblade": 0, "sparkle": 0, "aventurine": 0},
        "members": ["acheron", "mortenaxblade", "sparkle", "aventurine"],
    },
}

# 4★ avatar ids in our acheron-line casts (for inventory / gates)
FOUR_STAR_AVATARS = frozenset({"pela"})


def skill_levels_for_eidolon(character_id: str, eidolon: int) -> dict[str, int]:
    """Base 6/10/10/10 + E3/E5 SkillAdd (Pela E3 skill+2 basic+1; E5 ult+2 talent+2)."""
    basic, skill, ultimate, talent = 6, 10, 10, 10
    if character_id == "pela" and eidolon >= 3:
        skill += 2
        basic += 1  # capped at 10 by data; keep 7 for intent
        basic = min(basic, 10)
        skill = min(skill, 15)
    if character_id == "pela" and eidolon >= 5:
        ultimate += 2
        talent += 2
        ultimate = min(ultimate, 15)
        talent = min(talent, 15)
    if character_id == "acheron" and eidolon >= 3:
        # E3/E5 skill adds exist but main caliber is E2 — leave base
        pass
    return {"basic": basic, "skill": skill, "ultimate": ultimate, "talent": talent}


def pieces_to_nous_relics(
    pieces: dict[str, Any],
    *,
    cavern_slug: str,
    planar_slug: str,
) -> dict[str, dict[str, Any]]:
    cavern = set_slug_to_nous_id(cavern_slug)
    planar = set_slug_to_nous_id(planar_slug)
    out: dict[str, dict[str, Any]] = {}
    for slot, pb in pieces.items():
        set_id = planar if slot in ("sphere", "rope") else cavern
        main = AFFIX.get(pb.main)
        if main is None:
            raise KeyError(f"unmapped main affix {pb.main!r} on {slot}")
        subs: dict[str, int] = {}
        for k, rolls in (pb.subs or {}).items():
            nk = AFFIX.get(k)
            if nk is None:
                raise KeyError(f"unmapped sub affix {k!r} on {slot}")
            subs[nk] = int(rolls)
        out[slot] = {"set_id": set_id, "main": main, "subs": subs}
    return out


def _raw_meta(character_id: str) -> dict:
    for p in (
        REPO / f"data/hsr/characters/{character_id}.json",
        REPO / f"data/hsr/characters/supports/{character_id}.json",
    ):
        if p.is_file():
            return json.loads(p.read_text(encoding="utf-8")).get("_meta") or {}
    return {}


def build_our_and_nous_member(
    character_id: str,
    *,
    team_key: str = "acheron_direct",
) -> tuple[Character, CharacterLoadoutResult, dict[str, Any]]:
    """Return (our Character after loadout, loadout report, Nous member dict)."""
    cal = TEAM_CALIBER[team_key]
    cfg_root = load_loadout_config(cal["loadout"])
    if cfg_root is None:
        raise FileNotFoundError(cal["loadout"])
    ccfg = dict(cfg_root["characters"][character_id])
    spd = cal["speed"].get(character_id)
    if spd is not None:
        ccfg["target_speed"] = spd
    char = load_character_by_id(character_id)
    paths: dict[str, str] = {}
    for mid in cal["members"]:
        c = load_character_by_id(mid)
        paths[mid] = c.path.value if hasattr(c.path, "value") else str(c.path)
    char, rep = apply_benchmark_to_character(
        char,
        character_id=character_id,
        cfg=ccfg,
        raw_meta=_raw_meta(character_id),
        team_paths=paths,
    )
    eidolon = int(cal["eidolons"].get(character_id, 0))
    # Panel gate: strip S1 ATK so equipment-only panels align; S delta reported separately.
    if character_id == "jiaoqiu" and float(getattr(char, "hearth_kindle_atk_flat", 0.0) or 0.0) > 0:
        flat = float(char.hearth_kindle_atk_flat)
        char.build.stats.atk = float(char.build.stats.atk) - flat
    sets = ccfg.get("relic_sets") or {}
    relics = pieces_to_nous_relics(
        rep.pieces,
        cavern_slug=str(sets["cavern"]),
        planar_slug=str(sets["planar"]),
    )
    aid = AVATAR_ID[character_id]
    lc = int((ccfg.get("light_cone") or {}).get("id") or LC_ID[character_id])
    si = int((ccfg.get("light_cone") or {}).get("superimposition") or 1)
    skill_levels = skill_levels_for_eidolon(character_id, eidolon)
    member: dict[str, Any] = {
        "character_template": aid,
        "level": 80,
        "eidolon": eidolon,
        "skill_levels": skill_levels,
        "light_cone_template": str(lc),
        "light_cone": {"level": 80, "superimposition": si},
        "relics": relics,
    }
    if character_id == "sparkle":
        member["version"] = "enhanced"
    return char, rep, member


def nous_team_members(team_key: str) -> list[dict[str, Any]]:
    cal = TEAM_CALIBER[team_key]
    members = []
    for mid in cal["members"]:
        _, _, m = build_our_and_nous_member(mid, team_key=team_key)
        members.append(m)
    return members
