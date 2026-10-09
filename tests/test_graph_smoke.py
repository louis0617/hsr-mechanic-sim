# -*- coding: utf-8 -*-
"""Graph compile smoke + frozen F1 checkpoint presence."""
from __future__ import annotations

import json
from pathlib import Path

from hsrsim.catalog.loader import load_character_by_id
from hsrsim.graph.compiler import HSRGraphCompiler

ROOT = Path(__file__).resolve().parents[1]


def test_compile_acheron_graph():
    char = load_character_by_id("acheron")
    g = HSRGraphCompiler().compile(char)
    assert g.number_of_nodes() > 0
    assert g.number_of_edges() >= 0


def test_frozen_extraction_f1_summary():
    ck = json.loads((ROOT / "results" / "eval_extraction_checkpoint.json").read_text(encoding="utf-8"))
    s = ck["summary"]
    assert s["n_chars"] == 5
    assert abs(s["mean_aligned_edge_f1"] - 0.7053962233675463) < 1e-9
    assert abs(s["mean_node_f1"] - 0.7486019862490451) < 1e-9
