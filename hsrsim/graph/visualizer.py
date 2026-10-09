"""
Graph visualizer. Produces paper-ready figures.

Usage:
    G = HSRGraphCompiler().compile(acheron)
    fig = visualize(G, save_to="figures/acheron_graph.png")
"""
from __future__ import annotations

from typing import Optional

import matplotlib.pyplot as plt
import networkx as nx


# Colors per node type (consistent across paper figures)
NODE_COLORS = {
    "zone":  "#FF6B6B",   # red — multiplier zones (the "grid" of the graph)
    "skill": "#4ECDC4",   # teal — actions (player-controllable)
    "state": "#FFD93D",   # yellow — resources & effects (game state)
}

# Edge styles per type
EDGE_STYLES = {
    "trigger":    {"color": "#2E86AB", "style": "solid", "width": 2.0},
    "dependency": {"color": "#A23B72", "style": "dashed", "width": 1.5},
    "buff":       {"color": "#F18F01", "style": "solid", "width": 1.0},
    "consume":    {"color": "#7F7F7F", "style": "dotted", "width": 1.0},
    "produce":    {"color": "#16A34A", "style": "solid", "width": 1.5},
    "require":    {"color": "#7C3AED", "style": "dashed", "width": 1.5},
    "feeds":      {"color": "#9CA3AF", "style": "solid", "width": 1.1},
}


def _configure_cjk_font() -> None:
    """Prefer a CJK font on Windows/macOS so name_zh labels render in figures."""
    from matplotlib import font_manager

    for name in ("Microsoft YaHei", "SimHei", "PingFang SC", "Noto Sans CJK SC"):
        if any(f.name == name for f in font_manager.fontManager.ttflist):
            plt.rcParams["font.sans-serif"] = [name, "DejaVu Sans"]
            plt.rcParams["axes.unicode_minus"] = False
            return


def visualize(
    G: nx.MultiDiGraph,
    save_to: Optional[str] = None,
    figsize: tuple[float, float] = (14, 10),
    layout: str = "spring",
    title: Optional[str] = None,
) -> plt.Figure:
    """Draw a heterogeneous graph with type-specific styling.
    
    Args:
        G: graph from HSRGraphCompiler.compile()
        save_to: file path; if given, also saves to disk
        layout: "spring" (organic), "shell" (concentric), "kamada_kawai" (force-directed)
        title: figure title; defaults to character name
    """
    fig, ax = plt.subplots(figsize=figsize)
    _configure_cjk_font()
    
    # Compute layout
    if layout == "shell":
        # Group by type for cleaner concentric layout
        groups = {"zone": [], "skill": [], "state": []}
        for n, d in G.nodes(data=True):
            groups[d["type"]].append(n)
        pos = nx.shell_layout(G, nlist=[groups["zone"], groups["state"], groups["skill"]])
    elif layout == "kamada_kawai":
        pos = nx.kamada_kawai_layout(G)
    else:
        pos = nx.spring_layout(G, k=2.0, iterations=100, seed=42)
    
    # Draw nodes by type
    for ntype, color in NODE_COLORS.items():
        nodes = [n for n, d in G.nodes(data=True) if d["type"] == ntype]
        nx.draw_networkx_nodes(
            G, pos, nodelist=nodes, node_color=color, node_size=900,
            edgecolors="black", linewidths=1.0, alpha=0.85, ax=ax,
        )
    
    # Draw edges by type (dependency width scales with |modifier|)
    for etype, style in EDGE_STYLES.items():
        typed = [(u, v, d) for u, v, d in G.edges(data=True) if d["type"] == etype]
        for u, v, d in typed:
            width = style["width"]
            if etype == "dependency":
                try:
                    width = min(4.0, 1.2 + abs(float(d.get("weight") or 0)) * 4.0)
                except (TypeError, ValueError):
                    width = style["width"]
            nx.draw_networkx_edges(
                G, pos, edgelist=[(u, v)],
                edge_color=style["color"], style=style["style"], width=width,
                arrows=True, arrowsize=15, ax=ax, alpha=0.7,
            )
    
    # Labels (Chinese names if available)
    labels = {n: d.get("name_zh", n.split(":")[-1]) for n, d in G.nodes(data=True)}
    nx.draw_networkx_labels(G, pos, labels=labels, font_size=8, ax=ax)
    
    # Title and legend
    char_name = G.graph.get("character_name", "Unknown")
    ax.set_title(title or f"Heterogeneous Graph: {char_name}", fontsize=13)
    ax.axis("off")
    
    # Custom legend
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
    legend_elements = [
        Patch(facecolor=NODE_COLORS["zone"],  label="Zone (V_zone)"),
        Patch(facecolor=NODE_COLORS["skill"], label="Skill (V_skill)"),
        Patch(facecolor=NODE_COLORS["state"], label="State (V_state)"),
    ]
    legend_elements += [
        Line2D([0], [0], color=s["color"], linestyle=s["style"], lw=s["width"], label=t.title())
        for t, s in EDGE_STYLES.items()
    ]
    ax.legend(handles=legend_elements, loc="upper left", fontsize=8, framealpha=0.9)
    
    plt.tight_layout()
    if save_to:
        fig.savefig(save_to, dpi=200, bbox_inches="tight")
    return fig
