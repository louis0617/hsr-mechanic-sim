"""Semantic node alignment for edge F1 (LLM graph vs ground truth).

Used by eval_extraction.py and diagnose_edge_alignment.py.
Alignment uses graph structure only (action_type, dependency zones, consume patterns).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable

import networkx as nx


def edge_triple_set(G: nx.MultiDiGraph) -> set[tuple[str, str, str]]:
    return {(u, v, d.get("type", "")) for u, v, d in G.edges(data=True)}


def raw_edge_f1(extracted: nx.MultiDiGraph, ground_truth: nx.MultiDiGraph) -> float:
    """Legacy: exact (source, target, type) match — IDs must be identical."""
    return _f1_from_sets(edge_triple_set(extracted), edge_triple_set(ground_truth))


def remap_edge_set(
    G: nx.MultiDiGraph,
    mapping: dict[str, str],
) -> set[tuple[str, str, str]]:
    edges: set[tuple[str, str, str]] = set()
    for u, v, d in G.edges(data=True):
        u2 = mapping.get(u, u)
        v2 = mapping.get(v, v)
        edges.add((u2, v2, d.get("type", "")))
    return edges


def aligned_edge_f1(
    extracted: nx.MultiDiGraph,
    ground_truth: nx.MultiDiGraph,
    alignment: dict[str, str],
) -> float:
    ext_e = remap_edge_set(extracted, alignment)
    gt_e = edge_triple_set(ground_truth)
    return _f1_from_sets(ext_e, gt_e)


def _f1_from_sets(pred: set[tuple[str, str, str]], gold: set[tuple[str, str, str]]) -> float:
    tp = len(pred & gold)
    precision = tp / len(pred) if pred else 0.0
    recall = tp / len(gold) if gold else 0.0
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def _nodes_of(G: nx.MultiDiGraph, *, node_type: str | None = None, state_kind: str | None = None) -> list[str]:
    out: list[str] = []
    for n, d in G.nodes(data=True):
        if node_type is not None and d.get("type") != node_type:
            continue
        if state_kind is not None and d.get("state_kind") != state_kind:
            continue
        out.append(n)
    return sorted(out)


def _out_degree(G: nx.MultiDiGraph, node: str) -> int:
    return G.out_degree(node) if node in G else 0


def _dependency_zones(G: nx.MultiDiGraph, effect_node: str) -> frozenset[str]:
    zones: set[str] = set()
    for _u, v, d in G.out_edges(effect_node, data=True):
        if d.get("type") == "dependency" and str(v).startswith("zone:"):
            zones.add(v)
    return frozenset(zones)


def _skills_by_action_type(G: nx.MultiDiGraph) -> dict[str, list[str]]:
    buckets: dict[str, list[str]] = defaultdict(list)
    for n, d in G.nodes(data=True):
        if d.get("type") == "skill":
            buckets[str(d.get("action_type", ""))].append(n)
    for k in buckets:
        buckets[k].sort()
    return dict(buckets)


def _greedy_pair_by_out_degree(
    ext: nx.MultiDiGraph,
    gt: nx.MultiDiGraph,
    ext_nodes: list[str],
    gt_nodes: list[str],
) -> dict[str, str]:
    pairing: dict[str, str] = {}
    used_gt: set[str] = set()
    for en in sorted(ext_nodes, key=lambda n: _out_degree(ext, n), reverse=True):
        best_gt: str | None = None
        best_diff: int | None = None
        for gn in gt_nodes:
            if gn in used_gt:
                continue
            diff = abs(_out_degree(ext, en) - _out_degree(gt, gn))
            if best_diff is None or diff < best_diff:
                best_diff = diff
                best_gt = gn
        if best_gt is not None:
            pairing[en] = best_gt
            used_gt.add(best_gt)
    return pairing


def _align_skill_nodes(
    extracted: nx.MultiDiGraph,
    ground_truth: nx.MultiDiGraph,
) -> dict[str, str]:
    alignment: dict[str, str] = {}
    ext_by = _skills_by_action_type(extracted)
    gt_by = _skills_by_action_type(ground_truth)
    for action_type in set(ext_by) | set(gt_by):
        ext_list = ext_by.get(action_type, [])
        gt_list = gt_by.get(action_type, [])
        if len(ext_list) == 1 and len(gt_list) == 1:
            alignment[ext_list[0]] = gt_list[0]
        elif ext_list and gt_list:
            alignment.update(_greedy_pair_by_out_degree(extracted, ground_truth, ext_list, gt_list))
    return alignment


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _align_effect_nodes(
    extracted: nx.MultiDiGraph,
    ground_truth: nx.MultiDiGraph,
) -> dict[str, str]:
    ext_effects = _nodes_of(extracted, state_kind="effect")
    gt_effects = _nodes_of(ground_truth, state_kind="effect")
    alignment: dict[str, str] = {}
    used_gt: set[str] = set()

    ext_zones = {n: _dependency_zones(extracted, n) for n in ext_effects}
    gt_zones = {n: _dependency_zones(ground_truth, n) for n in gt_effects}

    for en in ext_effects:
        exact = [gn for gn in gt_effects if gn not in used_gt and ext_zones[en] == gt_zones[gn]]
        if len(exact) == 1:
            alignment[en] = exact[0]
            used_gt.add(exact[0])
            continue

    remaining_ext = [n for n in ext_effects if n not in alignment]
    remaining_gt = [n for n in gt_effects if n not in used_gt]
    for en in remaining_ext:
        best_gt: str | None = None
        best_score = -1.0
        for gn in remaining_gt:
            if gn in used_gt:
                continue
            score = _jaccard(ext_zones[en], gt_zones[gn])
            if score > best_score:
                best_score = score
                best_gt = gn
        if best_gt is not None and best_score > 0:
            alignment[en] = best_gt
            used_gt.add(best_gt)
            remaining_gt.remove(best_gt)

    return alignment


def _consume_skill_set(
    G: nx.MultiDiGraph,
    var_node: str,
    skill_map: dict[str, str],
) -> frozenset[str]:
    skills: set[str] = set()
    for u, _v, d in G.in_edges(var_node, data=True):
        if d.get("type") == "consume" and str(u).startswith("skill:"):
            skills.add(skill_map.get(u, u))
    return frozenset(skills)


def _align_variable_nodes(
    extracted: nx.MultiDiGraph,
    ground_truth: nx.MultiDiGraph,
    skill_alignment: dict[str, str],
) -> dict[str, str]:
    ext_vars = _nodes_of(extracted, state_kind="variable")
    gt_vars = _nodes_of(ground_truth, state_kind="variable")
    alignment: dict[str, str] = {}
    used_gt: set[str] = set()

    if len(ext_vars) == 1 and len(gt_vars) == 1:
        alignment[ext_vars[0]] = gt_vars[0]
        return alignment

    ext_patterns = {n: _consume_skill_set(extracted, n, skill_alignment) for n in ext_vars}
    gt_patterns = {n: _consume_skill_set(ground_truth, n, {}) for n in gt_vars}

    for en in ext_vars:
        exact = [gn for gn in gt_vars if gn not in used_gt and ext_patterns[en] == gt_patterns[gn]]
        if len(exact) == 1:
            alignment[en] = exact[0]
            used_gt.add(exact[0])

    remaining_ext = [n for n in ext_vars if n not in alignment]
    remaining_gt = [n for n in gt_vars if n not in used_gt]
    if len(remaining_ext) == 1 and len(remaining_gt) == 1:
        alignment[remaining_ext[0]] = remaining_gt[0]

    return alignment


@dataclass
class AlignmentStats:
    skill_aligned: int = 0
    effect_aligned: int = 0
    variable_aligned: int = 0
    unmatched_ext: int = 0
    unmatched_gt: int = 0
    alignment: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, int | dict[str, str]]:
        return {
            "skill_aligned": self.skill_aligned,
            "effect_aligned": self.effect_aligned,
            "variable_aligned": self.variable_aligned,
            "unmatched_ext": self.unmatched_ext,
            "unmatched_gt": self.unmatched_gt,
        }


def _character_specific_nodes(G: nx.MultiDiGraph) -> set[str]:
    specific: set[str] = set()
    for n, d in G.nodes(data=True):
        if d.get("type") == "skill":
            specific.add(n)
        elif d.get("state_kind") in ("effect", "variable"):
            specific.add(n)
    return specific


def build_node_alignment(
    extracted: nx.MultiDiGraph,
    ground_truth: nx.MultiDiGraph,
) -> AlignmentStats:
    """Map extracted node IDs → GT node IDs by structural semantics."""
    alignment: dict[str, str] = {}

    skill_map = _align_skill_nodes(extracted, ground_truth)
    alignment.update(skill_map)

    effect_map = _align_effect_nodes(extracted, ground_truth)
    alignment.update(effect_map)

    var_map = _align_variable_nodes(extracted, ground_truth, skill_map)
    alignment.update(var_map)

    ext_specific = _character_specific_nodes(extracted)
    gt_specific = _character_specific_nodes(ground_truth)
    matched_ext = set(alignment.keys())
    matched_gt = set(alignment.values())

    return AlignmentStats(
        skill_aligned=len(skill_map),
        effect_aligned=len(effect_map),
        variable_aligned=len(var_map),
        unmatched_ext=len(ext_specific - matched_ext),
        unmatched_gt=len(gt_specific - matched_gt),
        alignment=alignment,
    )


def evaluate_edge_scores(
    extracted: nx.MultiDiGraph,
    ground_truth: nx.MultiDiGraph,
) -> tuple[float, float, AlignmentStats]:
    """Return (aligned edge_f1, raw_edge_f1, alignment stats)."""
    stats = build_node_alignment(extracted, ground_truth)
    raw = raw_edge_f1(extracted, ground_truth)
    aligned = aligned_edge_f1(extracted, ground_truth, stats.alignment)
    return aligned, raw, stats
