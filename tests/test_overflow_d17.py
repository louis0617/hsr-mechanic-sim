"""L1-T26..T28: fill-then-spend overflow excess formula (D1.7)."""
from __future__ import annotations

import math

from hsrsim.analytic.overflow import average_excess


def _close(actual: float, expected: float) -> None:
    assert math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-9), (
        f"{actual} != {expected}"
    )


def test_L1_T26_constant_gain_30_consume_90():
    """X≡30 → excess=15, effective consume=105."""
    excess = average_excess([(1.0, 30.0)])
    _close(excess, 15.0)
    _close(90.0 + excess, 105.0)


def test_L1_T27_mixed_20_and_30():
    """X∈{20,30} equally → E[X]=25, E[X²]=650, excess=13, effective=113."""
    excess = average_excess([(0.5, 20.0), (0.5, 30.0)])
    _close(excess, 13.0)
    _close(100.0 + excess, 113.0)


def test_L1_T28_unit_gains_consume_9():
    """X≡1 → excess=0.5."""
    excess = average_excess([(1.0, 1.0)])
    _close(excess, 0.5)
    _close(9.0 + excess, 9.5)
