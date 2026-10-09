"""Cross-character mechanism isolation checks for RQ1 E1 (Figure E1).

Mechanism isolation: a support's setup skill cannot form the axis fragment
    support_setup --trigger--> state --dependency--> zone <--buff-- main_burst
because the support buff zones do not intersect zones the main DPS burst reads.
"""
from __future__ import annotations

from dataclasses import dataclass

import networkx as nx

from hsrsim.graph.compiler import HSRGraphCompiler
from hsrsim.simulator.types import Character

# Mirrors compiler STAT_TO_ZONE with E1-only extensions for stats not yet in compiler.
STAT_TO_ZONE: dict[str, str] = {
    "def_reduction": "f_def",
    "def_ignore": "f_def",
    "vuln_apply": "f_vuln",
    "vuln": "f_vuln",
    "dmg_boost": "f_dmgBoost",
    "crit_rate": "f_crit",
    "crit_dmg": "f_crit",
    "res_pen": "f_res",
    "break_effect": "f_be",
    "super_break_boost": "f_sbBoost",
    "super_break_boost_pct": "f_sbBoost",
    "dot_dmg_boost": "f_dotBoost",
    "dot_boost": "f_dotBoost",
    "weaken": "f_weaken",
    "weaken_pct": "f_weaken",
    "original_mult": "f_origMult",
    "elation": "f_elation",
    "punchline": "f_punchline",
    "merrymake": "f_merrymake",
    "true_followup": "true_followup",  # TRUE pathway side-channel (not in ZONE_REGISTRY)
}


def _zone_node(zone_id: str) -> str:
    return f"zone:{zone_id}"


def _skill_node(skill_id: str) -> str:
    return f"skill:{skill_id}"


def zones_read_by_skill(G: nx.MultiDiGraph, skill_id: str) -> set[str]:
    """Zones a skill reads via buff edges (damage pathway)."""
    node = _skill_node(skill_id)
    out: set[str] = set()
    for _u, v, data in G.out_edges(node, data=True):
        if data.get("type") == "buff" and v.startswith("zone:"):
            out.add(v.split(":", 1)[1])
    return out


def zones_activated_by_skill(G: nx.MultiDiGraph, skill_id: str) -> set[str]:
    """Zones modified when a setup skill triggers its effects (trigger → dependency)."""
    setup = _skill_node(skill_id)
    zones: set[str] = set()
    for _u, state, data in G.out_edges(setup, data=True):
        if data.get("type") != "trigger":
            continue
        for _s, zone, d2 in G.out_edges(state, data=True):
            if d2.get("type") == "dependency" and zone.startswith("zone:"):
                zones.add(zone.split(":", 1)[1])
            elif d2.get("type") == "dependency":
                # E1 extension nodes (e.g. true_followup without zone: prefix)
                tgt = d2.get("target_stat", "")
                base = tgt.split(".", 1)[0]
                zid = STAT_TO_ZONE.get(base)
                if zid:
                    zones.add(zid)
    # Also read dependency from effect modifiers directly if graph edges missing
    for _u, state, data in G.out_edges(setup, data=True):
        if data.get("type") != "trigger":
            continue
        for _s, zone, d2 in G.out_edges(state, data=True):
            if d2.get("type") == "dependency":
                zid = zone.split(":", 1)[1] if zone.startswith("zone:") else zone
                zones.add(zid)
    return zones


def zones_from_effect_modifiers(char: Character, effect_ids: list[str]) -> set[str]:
    """Fallback zone set from effect modifier bags (when compiler lacks an edge)."""
    zones: set[str] = set()
    by_id = {e.id: e for e in char.build.effects}
    for eid in effect_ids:
        eff = by_id.get(eid)
        if eff is None:
            continue
        for mod in eff.modifiers:
            base = mod.target_stat.split(".", 1)[0]
            zid = STAT_TO_ZONE.get(base)
            if zid:
                zones.add(zid)
    return zones


def support_buff_zones(G: nx.MultiDiGraph, char: Character, setup_skill_id: str) -> set[str]:
    """Union of graph-derived and modifier-derived zones for a support setup skill."""
    action = next((a for a in char.build.actions if a.id == setup_skill_id), None)
    zones = zones_activated_by_skill(G, setup_skill_id)
    if action is not None:
        zones |= zones_from_effect_modifiers(char, action.applies_effects)
    return zones


def has_cross_axis_path(
    G_support: nx.MultiDiGraph,
    support_char: Character,
    setup_skill_id: str,
    G_main: nx.MultiDiGraph,
    main_burst_id: str,
) -> bool:
    """True iff support setup can structurally buff main burst through a shared zone."""
    sup_zones = support_buff_zones(G_support, support_char, setup_skill_id)
    main_zones = zones_read_by_skill(G_main, main_burst_id)
    return bool(sup_zones & main_zones)


@dataclass(frozen=True)
class MechanismPairing:
    main_id: str
    main_label: str
    main_tier: str
    main_burst: str
    support_id: str
    support_label: str
    support_tier: str
    support_setup: str


@dataclass
class MechanismIsolationResult:
    pairing: MechanismPairing
    isolated: bool
    support_zones: list[str]
    main_zones: list[str]
    overlap: list[str]


def evaluate_pairing(
    pairing: MechanismPairing,
    *,
    compiler: HSRGraphCompiler | None = None,
    load_char,
) -> MechanismIsolationResult:
    comp = compiler or HSRGraphCompiler()
    main_char = load_char(pairing.main_id)
    support_char = load_char(pairing.support_id)
    G_main = comp.compile(main_char)
    G_support = comp.compile(support_char)
    sup_z = support_buff_zones(G_support, support_char, pairing.support_setup)
    main_z = zones_read_by_skill(G_main, pairing.main_burst)
    overlap = sup_z & main_z
    connected = bool(overlap)
    return MechanismIsolationResult(
        pairing=pairing,
        isolated=not connected,
        support_zones=sorted(sup_z),
        main_zones=sorted(main_z),
        overlap=sorted(overlap),
    )
