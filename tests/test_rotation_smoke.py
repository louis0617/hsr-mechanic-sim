# -*- coding: utf-8 -*-
"""Smoke tests for rotation package (no 100-cycle search)."""
from __future__ import annotations

from hsrsim.rotation.community import community_jq_axis
from hsrsim.rotation.enumerate import iter_adaptive_axes
from hsrsim.rotation.schema import CycleAxis


def test_community_jq_axis_has_adaptive_members():
    axis = community_jq_axis()
    assert isinstance(axis, CycleAxis)
    assert "community" in axis.axis_id
    assert "jq" in axis.axis_id


def test_iter_adaptive_axes_jq_nonempty():
    axes = iter_adaptive_axes("jq")
    assert len(axes) >= 1
    assert all(isinstance(a, CycleAxis) for a in axes[:5])
