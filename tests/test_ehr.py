"""EHR field + debuff hit chance (D9)."""
from __future__ import annotations

from hsrsim.graph.loader import load_character_by_id
from hsrsim.simulator.ehr import debuff_hit_chance


def test_debuff_hit_chance_fribbels_formula():
    assert abs(debuff_hit_chance(1.0, 0.0, 0.0) - 1.0) < 1e-9
    assert abs(debuff_hit_chance(1.0, 0.67, 0.4) - 1.0) < 1e-9
    assert abs(debuff_hit_chance(0.6, 0.6, 0.3) - 0.672) < 1e-9


def test_jiaoqiu_loads_trace_ehr():
    jq = load_character_by_id("jiaoqiu")
    assert abs(jq.build.stats.ehr - 0.28) < 1e-9
