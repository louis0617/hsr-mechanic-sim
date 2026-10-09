"""Merge per-character graphs into a team graph with cross-role buff edges."""
from __future__ import annotations

import networkx as nx

from hsrsim.graph.compiler import HSRGraphCompiler
from hsrsim.simulator.types import Character, EffectTarget

_DEBUFF_TO_ZONE: dict[str, str] = {
    "def_reduction": "f_def",
    "def_ignore": "f_def",
    "vuln": "f_vuln",
    "vuln_apply": "f_vuln",
    "weaken": "f_weaken",
    "res": "f_res",
}


def _debuff_zones(effect) -> set[str]:
    zones: set[str] = set()
    for mod in effect.modifiers:
        base = mod.target_stat.split(".", 1)[0]
        zone = _DEBUFF_TO_ZONE.get(base)
        if zone:
            zones.add(zone)
    return zones


def compile_team_graph(members: list[Character]) -> nx.MultiDiGraph:
    """Compile 4 character graphs + cross_buff edges (support effect → ally zone)."""
    compiler = HSRGraphCompiler()
    G = nx.MultiDiGraph()
    main_ids = {c.id for c in members}

    for char in members:
        sub = compiler.compile(char)
        prefix = char.id
        for node, data in sub.nodes(data=True):
            G.add_node(f"{prefix}::{node}", **data, owner=char.id)
        for u, v, key, data in sub.edges(keys=True, data=True):
            G.add_edge(f"{prefix}::{u}", f"{prefix}::{v}", **data, owner=char.id)

        for eff in char.build.effects:
            if eff.target not in (EffectTarget.SINGLE_ENEMY, EffectTarget.ALL_ENEMIES):
                continue
            zones = _debuff_zones(eff)
            if not zones:
                continue
            src = f"{prefix}::state:effect:{eff.id}"
            if src not in G:
                continue
            for other in members:
                if other.id == char.id:
                    continue
                for zone_id in zones:
                    dst = f"{other.id}::zone:{zone_id}"
                    if dst in G:
                        G.add_edge(
                            src,
                            dst,
                            type="cross_buff",
                            owner_source=char.id,
                            owner_target=other.id,
                            effect_id=eff.id,
                        )
    G.graph["member_ids"] = list(main_ids)
    return G
