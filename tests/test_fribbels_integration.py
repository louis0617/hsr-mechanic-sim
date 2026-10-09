"""Tests for Fribbels hsr-optimizer integration (local checkout)."""
from __future__ import annotations

import pytest

from hsrsim.external.fribbels.formulas import calculate_def_multi
from hsrsim.external.fribbels.paths import FRIBBELS_ROOT
from hsrsim.external.fribbels.parser import (
    castorice_memo_skill_scaling,
    castorice_memo_talent_total_scaling,
    evernight_memo_hp_scaling,
    hyacine_heal_tally_scaling,
    load_character_fribbels,
)


pytestmark = pytest.mark.skipif(
    not FRIBBELS_ROOT.is_dir(),
    reason=f"Fribbels checkout missing at {FRIBBELS_ROOT}",
)


def test_fribbels_root_has_damage_calculator():
    calc = FRIBBELS_ROOT / "src/lib/optimization/engine/damage/damageCalculator.ts"
    assert calc.is_file()


def test_hyacine_heal_tally_c0():
    data = load_character_fribbels("hyacine")
    assert data.scalings_c0["memoSkillScaling"] == pytest.approx(0.20)
    assert data.defaults["healTallyMultiplier"] == 20
    assert hyacine_heal_tally_scaling(data.scalings_c0, 20) == pytest.approx(4.0)


def test_evernight_memo_scaling_at_16():
    data = load_character_fribbels("evernight")
    assert evernight_memo_hp_scaling(data.scalings_c0, 16) == pytest.approx(1.92)


def test_castorice_memo_defaults():
    data = load_character_fribbels("castorice")
    assert castorice_memo_skill_scaling(data.scalings_c0, 3) == pytest.approx(0.34)
    assert castorice_memo_talent_total_scaling(data.scalings_c0, 6) == pytest.approx(2.40)


def test_cyrene_true_dmg_buff():
    data = load_character_fribbels("cyrene")
    assert data.scalings_c0.get("skillTrueDmgBuff") == pytest.approx(0.24)


def test_def_multi_matches_fribbels_wgsl():
    # Fribbels: 100 / ((80+20) * (1-0) + 100) = 0.5 at Lv80 enemy, no def pen
    assert calculate_def_multi(80, 0.0) == pytest.approx(0.5)


def test_combo_parsed_for_castorice():
    data = load_character_fribbels("castorice")
    assert "DEFAULT_MEMO_TALENT" in data.combo_turn_abilities
    assert data.combo_turn_abilities.count("DEFAULT_MEMO_SKILL") >= 4
