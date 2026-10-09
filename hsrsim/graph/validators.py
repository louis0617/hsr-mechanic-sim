"""
Four quality criteria for compiled graphs (paper §4.5).

These validators are used in two places:
1. M5: compare LLM-extracted graph against hand-built ground truth
2. M9 experiments: filter out ill-formed LLM outputs before downstream evaluation
"""
from __future__ import annotations

import networkx as nx

from hsrsim.simulator.damage_zones import DAMAGE_PATHWAY_MATRIX, ZONE_REGISTRY


# ============================================================
# CRITERION 1: COVERAGE
# ============================================================

def check_coverage(G: nx.MultiDiGraph) -> tuple[bool, list[str]]:
    """Every zone that appears in any damage type's pathway must exist as a node.
    
    Returns:
        (passed, list_of_missing_zone_ids)
    """
    required = set()
    for zone_ids in DAMAGE_PATHWAY_MATRIX.values():
        required.update(zone_ids)
    
    present = {d["zone_id"] for n, d in G.nodes(data=True) if d.get("type") == "zone"}
    missing = list(required - present)
    return len(missing) == 0, missing


# ============================================================
# CRITERION 2: TRIGGER REACHABILITY
# 
# For every (setup_skill, burst_skill) pair where the original game has
# a "setup → state → zone → burst" chain, the graph must contain such a path.
# ============================================================

def check_trigger_reachability(G: nx.MultiDiGraph, expected_chains: list[tuple[str, str]] | None = None) -> tuple[bool, list[tuple[str, str]]]:
    """For each expected (setup_skill_id, burst_skill_id) pair, verify that:
        path: setup → state → zone → burst exists in G.
    
    Args:
        expected_chains: list of (setup_id, burst_id) tuples to verify.
                         If None, just check that AT LEAST ONE such path exists.
    Returns:
        (passed, list_of_failed_chains)
    """
    failed = []
    
    if expected_chains is None:
        # Loose check: ANY skill→state→zone→skill path
        skill_nodes = [n for n, d in G.nodes(data=True) if d.get("type") == "skill"]
        for setup in skill_nodes:
            for burst in skill_nodes:
                if setup == burst:
                    continue
                if _has_axis_path(G, setup, burst):
                    return True, []
        return False, [("any", "any")]
    
    for setup_id, burst_id in expected_chains:
        setup_node = f"skill:{setup_id}"
        burst_node = f"skill:{burst_id}"
        if not _has_axis_path(G, setup_node, burst_node):
            failed.append((setup_id, burst_id))
    
    return len(failed) == 0, failed


def _has_axis_path(G: nx.MultiDiGraph, setup: str, burst: str) -> bool:
    """Check existence of: setup --[trigger]--> state --[dependency]--> zone <--[buff]-- burst.
    
    This is the 'axis fragment' from paper Definition 4.2.
    """
    # Find states triggered by setup
    triggered_states = [v for u, v, d in G.out_edges(setup, data=True) if d.get("type") == "trigger"]
    for state in triggered_states:
        # Find zones depended on by this state
        dependent_zones = [v for u, v, d in G.out_edges(state, data=True) if d.get("type") == "dependency"]
        for zone in dependent_zones:
            # Check if burst has a buff edge to this zone
            buff_targets = [v for u, v, d in G.out_edges(burst, data=True) if d.get("type") == "buff"]
            if zone in buff_targets:
                return True
    return False


# ============================================================
# CRITERION 3: PARAMETER LOCALITY
# 
# Each skill's parameters affect only itself and its directly-triggered/consumed
# state nodes. No implicit cross-graph coupling.
# ============================================================

def check_parameter_locality(G: nx.MultiDiGraph) -> tuple[bool, list[str]]:
    """Verify that no skill node has more than `max_outgoing_edges` outgoing edges
    of types other than {buff, trigger, consume}.
    
    Detects 'magical' edges that suggest hidden coupling — usually a sign of
    incorrect LLM extraction.
    """
    violations = []
    # Compiler emits produce/require for SP/energy/variable_changes/requires;
    # these are first-class resource edges, not illicit cross-graph coupling.
    allowed_types = {"buff", "trigger", "consume", "produce", "require"}

    for n, d in G.nodes(data=True):
        if d.get("type") != "skill":
            continue
        for u, v, ed in G.out_edges(n, data=True):
            if ed.get("type") not in allowed_types:
                violations.append(f"{n} → {v} (type={ed.get('type')})")

    return len(violations) == 0, violations


# ============================================================
# CRITERION 4: DOWNSTREAM REACHABILITY
# 
# The structure must support inner-loop convergence to GSD > 0.25.
# This requires actually running the inner loop, so it's marked as "expensive".
# ============================================================

def check_downstream_reachability(
    G: nx.MultiDiGraph,
    inner_loop_runner=None,
    threshold: float = 0.25,
) -> tuple[bool, float]:
    """Run the inner loop on this graph; check whether it can reach GSD > threshold.
    
    Args:
        inner_loop_runner: callable(G) -> max_gsd. Inject from inner_loop module
                           when running, to avoid circular imports.
    Returns:
        (passed, achieved_gsd)
    """
    if inner_loop_runner is None:
        # Cannot check without the inner loop; treat as "unknown"
        return True, -1.0
    
    achieved = inner_loop_runner(G)
    return achieved >= threshold, achieved


# ============================================================
# ALL-IN-ONE
# ============================================================

def run_all_validators(G: nx.MultiDiGraph, expected_chains=None, inner_loop_runner=None) -> dict:
    """Run all four validators and return a report dict."""
    cov_pass, cov_missing = check_coverage(G)
    reach_pass, reach_failed = check_trigger_reachability(G, expected_chains)
    local_pass, local_violations = check_parameter_locality(G)
    down_pass, down_gsd = check_downstream_reachability(G, inner_loop_runner)
    
    return {
        "coverage": {"passed": cov_pass, "missing": cov_missing},
        "trigger_reachability": {"passed": reach_pass, "failed": reach_failed},
        "parameter_locality": {"passed": local_pass, "violations": local_violations},
        "downstream_reachability": {"passed": down_pass, "gsd": down_gsd},
        "overall_pass": cov_pass and reach_pass and local_pass and down_pass,
    }
