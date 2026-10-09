# -*- coding: utf-8 -*-
"""Unit tests for S1 Hearth Kindle formula (no full battle)."""
from __future__ import annotations

from hsrsim.rules.shared_gaps import (
    jiaoqiu_hearth_kindle_atk_flat,
    jiaoqiu_hearth_kindle_atk_pct,
)


def test_hearth_kindle_steps_and_cap():
    assert jiaoqiu_hearth_kindle_atk_pct(0.80) == 0.0
    assert jiaoqiu_hearth_kindle_atk_pct(0.949) == 0.0
    assert abs(jiaoqiu_hearth_kindle_atk_pct(0.95) - 0.6) < 1e-9
    assert abs(jiaoqiu_hearth_kindle_atk_pct(1.40) - 2.4) < 1e-9
    assert abs(jiaoqiu_hearth_kindle_atk_pct(1.412) - 2.4) < 1e-9
    assert abs(jiaoqiu_hearth_kindle_atk_flat(1.412, 1000.0) - 2400.0) < 1e-6
