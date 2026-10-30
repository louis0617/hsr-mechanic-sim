"""Tests for BattleState.clone() isolation."""
from __future__ import annotations

from hsrsim.graph.loader import load_character_by_id
from hsrsim.simulator.state import BattleState


def test_clone_does_not_mutate_original_hp():
    char = load_character_by_id("acheron")
    enemy = load_character_by_id("acheron")  # reuse as dummy ally/enemy shape
    state = BattleState.from_characters([char], [enemy])

    original_hp = state.allies[0].hp_current
    cloned = state.clone()
    cloned.allies[0].hp_current = 1.0

    assert state.allies[0].hp_current == original_hp


def test_clone_copies_variables_and_effects():
    char = load_character_by_id("acheron")
    state = BattleState.from_characters([char], [char])
    state.allies[0].variables["nihility_stacks"] = 5.0

    cloned = state.clone()
    cloned.allies[0].variables["nihility_stacks"] = 9.0

    assert state.allies[0].variables["nihility_stacks"] == 5.0
    assert cloned.sp_team_pool == state.sp_team_pool
    assert cloned.total_damage_dealt == state.total_damage_dealt
