"""Successive halving and coordinate descent.

Both take an evaluate callback. They do not run the simulator themselves.
"""
from __future__ import annotations

import math
from typing import Any, Callable


def successive_halving(
    candidates: list,
    evaluate: Callable[[Any, int], float],
    stages: list[tuple[int, float | int | None]] | None = None,
) -> dict:
    """20 rounds keep top 20%, then 50 rounds keep top 5, then 100 rounds.

    ``keep`` is a fraction in (0, 1], an integer count, or None (final stage).
    """
    if stages is None:
        stages = [(20, 0.2), (50, 5), (100, None)]
    pool = list(candidates)
    history: list[dict] = []
    for budget, keep in stages:
        scored = [(float(evaluate(item, budget)), item) for item in pool]
        scored.sort(key=lambda row: row[0], reverse=True)
        history.append(
            {
                "budget": budget,
                "n": len(scored),
                "best": scored[0][0] if scored else None,
            }
        )
        if keep is None:
            pool = [item for _, item in scored]
            break
        if isinstance(keep, float):
            n_keep = max(1, math.ceil(len(scored) * keep - 1e-12))
        else:
            n_keep = max(1, min(len(scored), int(keep)))
        pool = [item for _, item in scored[:n_keep]]
    return {"survivors": pool, "history": history}


def coordinate_descent(
    start: dict,
    dims: list[dict],
    evaluate: Callable[[dict], float],
) -> dict:
    """Vary one declared dimension at a time. Stop after a pass with no improvement."""
    current = dict(start)
    best = float(evaluate(current))
    evaluations = 1
    passes = 0
    improved = True
    while improved:
        improved = False
        passes += 1
        for dim in dims:
            name = dim["name"]
            chosen = current[name]
            chosen_score = best
            for value in dim.get("values") or []:
                if value == current[name]:
                    continue
                trial = dict(current)
                trial[name] = value
                score = float(evaluate(trial))
                evaluations += 1
                if score > chosen_score:
                    chosen_score = score
                    chosen = value
            if chosen != current[name]:
                current[name] = chosen
                best = chosen_score
                improved = True
    return {
        "point": current,
        "score": best,
        "evaluations": evaluations,
        "passes": passes,
    }
