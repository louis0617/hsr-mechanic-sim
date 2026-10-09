"""
Heterogeneous graph compiler. THE CORE OF RQ1.

Maps a character JSON (Pfau's B={S,A,E,V}) to a NetworkX heterogeneous graph
G = (V, E, φ, ψ, W) per the paper's formal definition.

This file is intentionally short — the heavy lifting is delegated to:
- damage_zones.py (defines V_zone via ZONE_REGISTRY)
- types.py (defines V_skill via Action, V_state via Stats+Variables+Effects)

The compiler's job is purely to wire them up by reading:
- Each Action's damage_instances → buff edges to applicable V_zones
- Each Action's applies_effects → trigger edges to V_state nodes
- Each Action's variable_changes → consume edges to V_state nodes
- Each Effect's modifiers → dependency edges from V_state to V_zone
"""
from __future__ import annotations

import networkx as nx
from typing import Literal

from hsrsim.simulator.damage_zones import DAMAGE_PATHWAY_MATRIX, ZONE_REGISTRY
from hsrsim.simulator.types import Action, ActionType, Build, Character, DamageType, Effect, Stats


# Node type mapping (φ in the paper)
NodeType = Literal["zone", "skill", "state"]
EdgeType = Literal["trigger", "dependency", "buff", "consume", "produce", "require"]

# Shared across kits for cross-character stack matching (残梦/负面供给)
TEAM_DEBUFF_FEED = "state:team_debuff_feed"
# Ally follow-up attacks feed kits that listen for teammate hits (热意/饲饵追击等)
TEAM_ATTACK_FEED = "state:team_attack_feed"


def is_follow_up_attack_action(action: Action) -> bool:
    """Genuine 追击/追加攻击 — excludes 欢愉技 (often mis-typed as follow_up)."""
    if not action.damage_instances:
        return False
    if any(di.damage_type == DamageType.ELATION for di in action.damage_instances):
        return False
    if action.type == ActionType.FOLLOW_UP:
        return True
    blob = f"{action.id} {action.name} {action.description or ''}"
    markers = ("追加攻击", "视为追加", "talent_fua", "follow_up", "follow-up", "_fua")
    return any(m in blob for m in markers)


class HSRGraphCompiler:
    """Compile a Character into a NetworkX heterogeneous graph.
    
    Usage:
        compiler = HSRGraphCompiler()
        G = compiler.compile(acheron_character)
        
        # Inspect
        zones = [n for n, d in G.nodes(data=True) if d['type'] == 'zone']
        rotation_edges = [(u, v) for u, v, d in G.edges(data=True) if d['type'] == 'buff']
        
        # Verify (M5: four quality criteria)
        validators.check_coverage(G)
        validators.check_trigger_reachability(G)
    """

    def compile(self, char: Character) -> nx.MultiDiGraph:
        """Compile a single character to a graph.
        
        Use MultiDiGraph because between two nodes there can be multiple edges
        of different types (e.g. an Action both triggers a state AND consumes another).
        """
        G = nx.MultiDiGraph(character_id=char.id, character_name=char.name)
        # Step 1: add all V_zone nodes (via reflection on damage_zones.py)
        self._add_zone_nodes(G)
        
        # Step 2: add V_state nodes (resources, effects)
        self._add_state_nodes(G, char.build)
        
        # Step 3: add V_skill nodes (one per action)
        self._add_skill_nodes(G, char.build)
        
        # Step 4: wire up edge types
        self._add_buff_edges(G, char.build)        # skill → zone
        self._add_trigger_edges(G, char.build)     # skill → state
        self._add_resource_edges(G, char.build)    # produce / consume / require
        self._add_dependency_edges(G, char.build)  # state → zone (via effect modifiers)
        
        return G

    # -----------------------------
    # Node addition
    # -----------------------------
    def _add_zone_nodes(self, G: nx.MultiDiGraph):
        for z in ZONE_REGISTRY:
            G.add_node(
                f"zone:{z.id}",
                type="zone",
                zone_id=z.id,
                name_zh=z.name_zh,
                name_en=z.name_en,
            )
        # Side-channel used by 真伤旁路 supports (昔涟 / 记忆开拓者等)
        G.add_node(
            "zone:true_followup",
            type="zone",
            zone_id="true_followup",
            name_zh="真伤旁路",
            name_en="True Follow-up",
        )

    def _add_state_nodes(self, G: nx.MultiDiGraph, build: Build):
        # Resource states
        if build.stats.energy_max > 0:
            G.add_node(
                "state:energy",
                type="state",
                state_kind="resource",
                max_value=build.stats.energy_max,
                name_zh="能量",
                name_en="Energy",
            )
        G.add_node(
            "state:sp_team",
            type="state",
            state_kind="resource",
            max_value=5,
            name_zh="战技点（队伍共享）",
            name_en="Skill Points (team shared)",
        )
        G.add_node(
            TEAM_DEBUFF_FEED,
            type="state",
            state_kind="resource",
            name_zh="敌方负面/叠层供给",
            name_en="Enemy debuff / stack feed",
        )
        G.add_node(
            TEAM_ATTACK_FEED,
            type="state",
            state_kind="resource",
            name_zh="队友攻击/行动供给",
            name_en="Ally attack / action feed",
        )
        G.add_node(
            "state:hp",
            type="state",
            state_kind="vital",
            max_value=build.stats.hp_max,
            name_zh="HP",
            name_en="HP",
        )
        G.add_node(
            "state:toughness",
            type="state",
            state_kind="vital",
            max_value=100.0,
            name_zh="韧性值",
            name_en="Toughness",
        )

        # Custom variables (Acheron's nihility_stacks, etc)
        for var in build.variables:
            G.add_node(
                f"state:var:{var.id}",
                type="state",
                state_kind="variable",
                max_value=var.max_value,
                name_zh=var.name,
                name_en=getattr(var, "name_en", None) or None,
            )

        # Effects (active or passive)
        for eff in build.effects:
            G.add_node(
                f"state:effect:{eff.id}",
                type="state",
                state_kind="effect",
                name_zh=eff.name,
                name_en=getattr(eff, "name_en", None) or None,
                is_buff=eff.is_buff,
            )

    def _add_skill_nodes(self, G: nx.MultiDiGraph, build: Build):
        for action in build.actions:
            G.add_node(
                f"skill:{action.id}",
                type="skill",
                action_type=action.type.value,
                name_zh=action.name,
                name_en=getattr(action, "name_en", None) or None,
                description=action.description,
            )

    # -----------------------------
    # Edge wiring
    # -----------------------------
    def _add_buff_edges(self, G: nx.MultiDiGraph, build: Build):
        """skill → zone: which zones this skill's damage reads from.
        
        Determined by the damage type's pathway matrix.
        """
        for action in build.actions:
            for dmg in action.damage_instances:
                applicable_zones = DAMAGE_PATHWAY_MATRIX[dmg.damage_type]
                for zid in applicable_zones:
                    G.add_edge(
                        f"skill:{action.id}",
                        f"zone:{zid}",
                        type="buff",
                        damage_type=dmg.damage_type.value,
                        weight=dmg.multiplier,
                    )

    def _add_trigger_edges(self, G: nx.MultiDiGraph, build: Build):
        """skill → state: which states this skill activates."""
        for action in build.actions:
            for eff_id in action.applies_effects:
                target_state_node = f"state:effect:{eff_id}"
                if target_state_node in G:
                    G.add_edge(
                        f"skill:{action.id}",
                        target_state_node,
                        type="trigger",
                    )

    def _add_resource_edges(self, G: nx.MultiDiGraph, build: Build):
        """skill → state: produce / consume / require (resources & stack feed)."""
        effect_by_id = {e.id: e for e in build.effects}
        for action in build.actions:
            sid = f"skill:{action.id}"
            if action.energy_cost != 0 and "state:energy" in G:
                et = "consume" if action.energy_cost > 0 else "produce"
                G.add_edge(
                    sid, "state:energy", type=et, weight=abs(action.energy_cost)
                )
            if action.sp_cost != 0 and "state:sp_team" in G:
                et = "consume" if action.sp_cost > 0 else "produce"
                G.add_edge(
                    sid, "state:sp_team", type=et, weight=abs(action.sp_cost)
                )
            for var_id, delta in action.variable_changes.items():
                state_node = f"state:var:{var_id}"
                if state_node not in G:
                    continue
                if delta > 0:
                    G.add_edge(sid, state_node, type="produce", weight=delta)
                elif delta < 0:
                    G.add_edge(sid, state_node, type="consume", weight=abs(delta))
            # Genuine follow-up / 追加攻击 only (欢愉技 excluded)
            if is_follow_up_attack_action(action):
                G.add_edge(
                    sid,
                    TEAM_ATTACK_FEED,
                    type="produce",
                    weight=1.0,
                )
            # Ally debuffs feed the shared stack/supply node (黄泉残梦等)
            if action.effect_target == "enemy":
                for eid in action.applies_effects:
                    eff = effect_by_id.get(eid)
                    if eff is not None and not eff.is_buff:
                        G.add_edge(
                            sid,
                            TEAM_DEBUFF_FEED,
                            type="produce",
                            weight=1.0,
                            via_effect=eid,
                        )
                        break
            # requires like "nihility_stacks>=9" → require var + team debuff feed
            for req in action.requires:
                base = req.split(">=")[0].split("<=")[0].split(">")[0].split("<")[0].strip()
                if base in {"team_attack_feed", "ally_attack_feed", "攻击供给"}:
                    G.add_edge(
                        sid,
                        TEAM_ATTACK_FEED,
                        type="require",
                        weight=1.0,
                        condition=req,
                    )
                    continue
                var_node = f"state:var:{base}"
                if var_node in G:
                    G.add_edge(sid, var_node, type="require", weight=1.0, condition=req)
                if base in {"nihility_stacks", "sliced_dream", "残梦"}:
                    G.add_edge(
                        sid,
                        TEAM_DEBUFF_FEED,
                        type="require",
                        weight=1.0,
                        condition=req,
                    )

    def _add_dependency_edges(self, G: nx.MultiDiGraph, build: Build):
        """state → zone: when an effect is active, which zones it modifies.
        
        Read from Effect.modifiers. e.g. a "DEF Reduction 30%" effect creates
        a dependency edge from that effect's state node to f_def.
        """
        # Mapping from modifier target_stat (base key) → zone ID
        STAT_TO_ZONE = {
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
            "true_followup": "true_followup",
        }
        for eff in build.effects:
            for mod in eff.modifiers:
                # Nested keys like "dmg_boost.fire" / "res_pen.lightning" → base stat
                base_stat = mod.target_stat.split(".")[0]
                zone_id = STAT_TO_ZONE.get(base_stat)
                if zone_id is None:
                    continue
                G.add_edge(
                    f"state:effect:{eff.id}",
                    f"zone:{zone_id}",
                    type="dependency",
                    target_stat=mod.target_stat,
                    operation=mod.operation,
                    weight=mod.value,
                )


# ============================================================
# CONVENIENCE
# ============================================================

def graph_summary(G: nx.MultiDiGraph) -> dict:
    """Quick stats for a compiled graph."""
    types = {"zone": 0, "skill": 0, "state": 0}
    edge_types = {
        "trigger": 0,
        "dependency": 0,
        "buff": 0,
        "consume": 0,
        "produce": 0,
        "require": 0,
    }
    for n, d in G.nodes(data=True):
        types[d["type"]] += 1
    for u, v, d in G.edges(data=True):
        et = d.get("type", "")
        if et in edge_types:
            edge_types[et] += 1
    return {
        "character": G.graph.get("character_name"),
        "nodes": types,
        "edges": edge_types,
        "total_nodes": G.number_of_nodes(),
        "total_edges": G.number_of_edges(),
    }
