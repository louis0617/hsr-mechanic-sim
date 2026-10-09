# -*- coding: utf-8 -*-
"""Generate Acheron kit graph DOT + PNG (optional) under docs/figures/."""
from __future__ import annotations

from pathlib import Path

from hsrsim.catalog.loader import load_character_by_id
from hsrsim.graph.compiler import HSRGraphCompiler

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "figures"
OUT.mkdir(parents=True, exist_ok=True)


def main() -> None:
    char = load_character_by_id("acheron")
    g = HSRGraphCompiler().compile(char)

    lines = [
        "digraph acheron {",
        "  rankdir=LR;",
        '  node [shape=box,fontsize=9];',
    ]
    colors = {"zone": "lightblue", "skill": "lightyellow", "state": "lightgrey"}
    for n, d in g.nodes(data=True):
        t = d.get("type", "?")
        label = str(n).replace('"', "")
        color = colors.get(t, "white")
        lines.append(
            f'  "{label}" [style=filled,fillcolor={color},label="{label}\\n({t})"];'
        )
    for u, v, d in g.edges(data=True):
        et = d.get("type", "")
        lines.append(f'  "{u}" -> "{v}" [label="{et}",fontsize=8];')
    lines.append("}")
    (OUT / "acheron_kit_graph.dot").write_text("\n".join(lines), encoding="utf-8")

    try:
        from hsrsim.graph.visualizer import visualize

        visualize(g, save_to=str(OUT / "acheron_kit_graph.png"), title="Acheron kit graph")
        png_ok = True
    except Exception as exc:  # matplotlib optional
        png_ok = False
        print("PNG skip:", exc)

    md = [
        "# Acheron kit graph (compiled)",
        "",
        f"Nodes: {g.number_of_nodes()}, edges: {g.number_of_edges()}.",
        "",
        "Source: `HSRGraphCompiler().compile(load_character_by_id(\"acheron\"))`.",
        "",
        "- DOT: [`acheron_kit_graph.dot`](acheron_kit_graph.dot)",
    ]
    if png_ok:
        md.append("- PNG: [`acheron_kit_graph.png`](acheron_kit_graph.png)")
    (OUT / "README.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("nodes", g.number_of_nodes(), "edges", g.number_of_edges(), "png", png_ok)


if __name__ == "__main__":
    main()
