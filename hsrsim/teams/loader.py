"""Team composition loading and team-level GSD evaluation ()."""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path

from hsrsim.enemies.benchmark_dummy import ally_element_keys, build_benchmark_dummy
from hsrsim.catalog.loader import load_character_by_id
from hsrsim.simulator.types import Character, Scenario

REPO_ROOT = Path(__file__).resolve().parents[2]
TEAMS_DIR = REPO_ROOT / "data" / "hsr" / "teams"

TEAM_IDS = (
    "acheron_direct",
    "firefly_super_break",
    "castorice_memory_true",
)

OPTIMAL_TEAM_IDS = TEAM_IDS

RANDOM_TEAM_IDS = (
    "acheron_random",
    "firefly_random",
    "castorice_random",
)

ALL_TEAM_IDS = OPTIMAL_TEAM_IDS + RANDOM_TEAM_IDS


@dataclass(frozen=True)
class TeamSpec:
    team_id: str
    name: str
    main_dps: str
    scenario: Scenario
    eidolons: dict[str, int]


def _require_eidolons(raw: dict, members: list[str]) -> dict[str, int]:
    """Scene must declare eidolon per member; no defaults (D5.2 / D5.3)."""
    if "eidolons" not in raw:
        raise ValueError(
            f"team {raw.get('team_id')!r}: missing required 'eidolons' "
            f"(must list every member; no default)"
        )
    eid = raw["eidolons"]
    if not isinstance(eid, dict):
        raise ValueError(f"team {raw.get('team_id')!r}: 'eidolons' must be an object")
    out: dict[str, int] = {}
    missing = [m for m in members if m not in eid]
    if missing:
        raise ValueError(
            f"team {raw.get('team_id')!r}: eidolons missing members {missing}"
        )
    extra = [k for k in eid if k not in members]
    if extra:
        raise ValueError(
            f"team {raw.get('team_id')!r}: eidolons has unknown members {extra}"
        )
    for mid in members:
        val = eid[mid]
        if not isinstance(val, int) or isinstance(val, bool) or not (0 <= val <= 6):
            raise ValueError(
                f"team {raw.get('team_id')!r}: eidolons[{mid!r}] must be int 0..6, got {val!r}"
            )
        out[mid] = val
    return out


def load_team_spec(team_id: str, *, apply_loadout: bool = True) -> TeamSpec:
    path = TEAMS_DIR / f"{team_id}.json"
    if not path.is_file():
        raise FileNotFoundError(f"Team spec not found: {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    members = list(raw["members"])
    eidolons = _require_eidolons(raw, members)
    allies = [load_character_by_id(member_id) for member_id in members]

    loadout_cfg = None
    if apply_loadout:
        from hsrsim.loadout.benchmark import apply_team_benchmark, load_loadout_config

        loadout_cfg = load_loadout_config(team_id)
        if loadout_cfg is not None:
            raw_metas: dict = {}
            from hsrsim.catalog.loader import CHARACTERS_DIR

            for mid in members:
                for cand in (
                    CHARACTERS_DIR / f"{mid}.json",
                    CHARACTERS_DIR / "supports" / f"{mid}.json",
                ):
                    if cand.is_file():
                        meta = json.loads(cand.read_text(encoding="utf-8")).get("_meta")
                        if meta:
                            raw_metas[mid] = meta
                        break
            allies, _reports = apply_team_benchmark(allies, team_id, raw_metas=raw_metas)

    #stamp eidolon onto each Character (LC stamped in apply_benchmark).
    stamped: list[Character] = []
    for ally in allies:
        stamped.append(
            ally.model_copy(update={"eidolon": int(eidolons[ally.id])})
        )
    allies = stamped

    from hsrsim.rules.wiring import assert_config_wired

    assert_config_wired(
        eidolons=eidolons,
        characters=allies,
        loadout=loadout_cfg,
    )

    enemy = build_benchmark_dummy(
        ally_elements=ally_element_keys(allies),
        immortal=False,
    )
    # Optional team override: narrow or replace weaknesses (still explicit on the same object).
    weaknesses = raw.get("enemy_weaknesses") or []
    if weaknesses:
        from hsrsim.enemies.benchmark_dummy import element_res_table

        enemy.weaknesses = list(weaknesses)
        enemy.build.stats.element_res = element_res_table(weaknesses)
    scenario = Scenario(
        name=raw.get("name", team_id),
        description=raw.get("description", ""),
        max_rounds=int(raw.get("max_rounds", 10)),
        # max_rounds is engine-round bookkeeping, NOT MoC cycles (C9).
        max_cycles=int(raw.get("max_cycles", 4)),
        allies=allies,
        enemies=[enemy],
        rotation=None,
        toughness_mode=raw.get("toughness_mode", "realistic"),
    )
    return TeamSpec(
        team_id=raw["team_id"],
        name=raw.get("name", team_id),
        main_dps=raw["main_dps"],
        scenario=scenario,
        eidolons=eidolons,
    )
