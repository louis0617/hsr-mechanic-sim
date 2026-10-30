"""Engine smoke test + requirement DSL."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from hsrsim.simulator.engine import Engine
from hsrsim.simulator.types import Scenario
from hsrsim.simulator.bots import GreedyBot, RandomBot

REPO = Path(__file__).resolve().parents[1]
SMOKE = REPO / "data" / "hsr" / "scenarios" / "acheron_smoke.json"


def test_acheron_smoke_end_to_end():
    scenario = Scenario.model_validate_json(SMOKE.read_text(encoding="utf-8"))
    result = Engine(scenario, ally_bot=GreedyBot(), enemy_bot=RandomBot()).run()
    assert result.total_damage > 0
    assert len(result.actions_taken) > 0


def test_requirement_blocks_ult_until_stacks():
    scenario = Scenario.model_validate_json(SMOKE.read_text(encoding="utf-8"))
    acheron = scenario.allies[0]
    ult = next(a for a in acheron.build.actions if a.id == "acheron_ult")
    engine = Engine(scenario)
    state = engine.state
    actor = state.allies[0]
    actor.variables["nihility_stacks"] = 8
    assert not engine._is_legal(actor, ult)
    actor.variables["nihility_stacks"] = 9
    assert engine._is_legal(actor, ult)
