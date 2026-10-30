"""
KQM cross-validation tests.

These tests are M2 acceptance criteria. If a damage zone is wrong, this catches it.
Reference numbers come from KQM SRL calculator with explicit configurations.

When updating a damage zone, run this and ensure all tests pass within 5% tolerance.
"""
from __future__ import annotations

import pytest

from hsrsim.simulator.damage_zones import (
    BaseZone,
    BrokenZone,
    CritZone,
    DamageContext,
    DefZone,
    DmgBoostZone,
    PunchlineZone,
    ResZone,
    VulnZone,
    compute_damage,
)
from hsrsim.simulator.types import DamageType, Element

from tests.kqm_acheron_baseline import (
    KQM_SIMPLE_STYGIAN_EXPECTED,
    KQM_TEAM_STYGIAN_EXPECTED,
    acheron_baseline_ctx,
    acheron_simple_stygian_ctx,
    kqm_srl_expected_damage,
)
from tests.kqm_firefly_kafka_baseline import (
    KQM_FIREFLY_SUPER_BREAK_EXPECTED,
    KQM_KAFKA_SHOCK_EXPECTED,
    firefly_super_break_ctx,
    kafka_shock_dot_ctx,
)


# ============================================================
# Helpers
# ============================================================

def make_ctx(**overrides) -> DamageContext:
    """Build a DamageContext with sensible defaults."""
    defaults = dict(
        attacker_level=80,
        attacker_atk=3000.0,
        attacker_crit_rate=0.0,
        attacker_crit_dmg=0.5,
        attacker_break_effect=0.0,
        attacker_dmg_boost_pct=0.0,
        attacker_res_pen_pct=0.0,
        attacker_def_ignore_pct=0.0,
        attacker_def_reduction_pct=0.0,
        defender_level=80,
        defender_def=1000.0,
        defender_res_pct=0.0,
        defender_vuln_pct=0.0,
        defender_mit_layers=[],
        defender_toughness_broken=False,
        skill_multiplier=2.0,
        damage_type=DamageType.DIRECT,
        element=Element.LIGHTNING,
    )
    defaults.update(overrides)
    return DamageContext(**defaults)


# ============================================================
# Tests for individual zones
# ============================================================

class TestDefZone:
    """Test the convex DEF formula. KEY: marginal returns must INCREASE with DEF reduction."""

    def test_no_reduction_same_level(self):
        ctx = make_ctx(attacker_level=80, defender_level=80, attacker_def_reduction_pct=0.0)
        # f_def = 100 / (100*1 + 100) = 0.5
        assert abs(DefZone().compute(ctx) - 0.5) < 1e-6

    def test_full_ignore_same_level(self):
        ctx = make_ctx(attacker_level=80, defender_level=80, attacker_def_ignore_pct=1.0)
        # f_def = 100 / (100*0 + 100) = 1.0
        assert abs(DefZone().compute(ctx) - 1.0) < 1e-6

    def test_marginal_returns_increase(self):
        """Convex check: Δf at high reduction > Δf at low reduction."""
        z = DefZone()
        f0 = z.compute(make_ctx(attacker_def_reduction_pct=0.0))
        f20 = z.compute(make_ctx(attacker_def_reduction_pct=0.20))
        f60 = z.compute(make_ctx(attacker_def_reduction_pct=0.60))
        f80 = z.compute(make_ctx(attacker_def_reduction_pct=0.80))
        delta_low = f20 - f0
        delta_high = f80 - f60
        assert delta_high > delta_low, f"Convexity broken: Δlow={delta_low}, Δhigh={delta_high}"


class TestCritZone:
    """Crit zone: handles expected value, hard cap, DOT exemption."""

    def test_no_crit(self):
        ctx = make_ctx(attacker_crit_rate=0.0, attacker_crit_dmg=0.5)
        assert abs(CritZone().compute(ctx) - 1.0) < 1e-6

    def test_full_crit(self):
        ctx = make_ctx(attacker_crit_rate=1.0, attacker_crit_dmg=1.0)
        assert abs(CritZone().compute(ctx) - 2.0) < 1e-6

    def test_hard_cap(self):
        """CR > 1.0 should be clamped."""
        ctx = make_ctx(attacker_crit_rate=1.5, attacker_crit_dmg=1.0)
        # Capped at 1.0, so f = 1.0 * 2.0 + 0 * 1.0 = 2.0
        assert abs(CritZone().compute(ctx) - 2.0) < 1e-6

    def test_dot_does_not_crit(self):
        """DOT should not apply crit zone."""
        assert not CritZone().applies_to(DamageType.DOT)
        assert not CritZone().applies_to(DamageType.BREAK)
        assert not CritZone().applies_to(DamageType.SUPER_BREAK)
        assert CritZone().applies_to(DamageType.DIRECT)
        assert CritZone().applies_to(DamageType.ELATION)


class TestPunchlineZone:
    """v4.0 Punchline: 1 + 5P/(P+240). Hyperbolic, asymptote at 6.0."""

    def test_zero(self):
        ctx = make_ctx(punchline_value=0.0)
        assert abs(PunchlineZone().compute(ctx) - 1.0) < 1e-6

    def test_240(self):
        # P=240: 1 + 5*240/480 = 1 + 2.5 = 3.5
        ctx = make_ctx(punchline_value=240.0)
        assert abs(PunchlineZone().compute(ctx) - 3.5) < 1e-6

    def test_diminishing_returns(self):
        z = PunchlineZone()
        delta_low = z.compute(make_ctx(punchline_value=100)) - z.compute(make_ctx(punchline_value=0))
        delta_high = z.compute(make_ctx(punchline_value=2000)) - z.compute(make_ctx(punchline_value=1900))
        assert delta_low > delta_high


class TestBrokenZone:
    def test_unbroken(self):
        ctx = make_ctx(defender_toughness_broken=False)
        assert abs(BrokenZone().compute(ctx) - 0.9) < 1e-6

    def test_broken(self):
        ctx = make_ctx(defender_toughness_broken=True)
        assert abs(BrokenZone().compute(ctx) - 1.0) < 1e-6


# ============================================================
# Integration: full pathway
# ============================================================

class TestPathwayIntegration:
    """Test that compute_damage correctly assembles the full pipeline."""

    def test_direct_uses_all_zones(self):
        ctx = make_ctx(damage_type=DamageType.DIRECT)
        damage, breakdown = compute_damage(ctx)
        # Should include all 10 standard zones
        expected_zones = {"f_base", "f_origMult", "f_crit", "f_dmgBoost", "f_weaken",
                          "f_def", "f_res", "f_vuln", "f_mit", "f_broken", "f_trueDmg"}
        assert set(breakdown.keys()) == expected_zones

    def test_dot_excludes_crit(self):
        ctx = make_ctx(damage_type=DamageType.DOT)
        _, breakdown = compute_damage(ctx)
        assert "f_crit" not in breakdown
        assert "f_origMult" not in breakdown
        assert "f_weaken" not in breakdown
        assert "f_dmgBoost" in breakdown  # DOT does use dmg boost!
        assert "f_trueDmg" in breakdown  # 141502 each DMG instance

    def test_true_bypasses_everything(self):
        ctx = make_ctx(damage_type=DamageType.TRUE, extra_flat_damage=5000.0)
        damage, breakdown = compute_damage(ctx)
        assert damage == 5000.0
        # Only the synthetic flat field should be in breakdown
        assert breakdown == {"f_true_flat": 5000.0}

    def test_elation_uses_3_new_zones(self):
        ctx = make_ctx(damage_type=DamageType.ELATION)
        _, breakdown = compute_damage(ctx)
        assert "f_elation" in breakdown
        assert "f_punchline" in breakdown
        assert "f_merrymake" in breakdown
        assert "f_dmgBoost" not in breakdown   # Elation breaks dmg boost
        assert "f_weaken" not in breakdown      # and weaken
        assert "f_crit" in breakdown            # but keeps crit

    def test_super_break_partially_coupled(self):
        """Validate paper's 'partial coupling' claim: 5 shared, 3 broken, 3 unique."""
        ctx_direct = make_ctx(damage_type=DamageType.DIRECT)
        ctx_sb = make_ctx(damage_type=DamageType.SUPER_BREAK)
        _, b_direct = compute_damage(ctx_direct)
        _, b_sb = compute_damage(ctx_sb)
        
        shared = set(b_direct.keys()) & set(b_sb.keys())
        # Shared: f_def, f_res, f_vuln, f_mit, f_broken (5 zones, per paper claim)
        assert {"f_def", "f_res", "f_vuln", "f_mit", "f_broken"}.issubset(shared)
        
        broken = set(b_direct.keys()) - set(b_sb.keys())
        # Broken: f_crit, f_dmgBoost, f_weaken (3 zones)
        assert {"f_crit", "f_dmgBoost", "f_weaken"}.issubset(broken)
        
        unique_to_sb = set(b_sb.keys()) - set(b_direct.keys())
        assert {"f_be", "f_sbBoost"}.issubset(unique_to_sb)


# ============================================================
# Reference values (M2 acceptance: must match KQM within 5%)
# ============================================================

class TestKQMReferenceValues:
    """M2 acceptance: damage must match KQM SRL formulas within 5%."""

    def test_acheron_baseline(self):
        """黄泉 C0E0：Stygian Resurge 一段 vs Lv95（开拓者护盾队 buff 已折叠进 ctx）。"""
        # Hand-verifiable sub-anchor (KQM damage-formula page, no team buffs).
        simple = acheron_simple_stygian_ctx()
        simple_dmg, _ = compute_damage(simple)
        assert abs(simple_dmg - KQM_SIMPLE_STYGIAN_EXPECTED) < 1.0
        assert abs(simple_dmg - kqm_srl_expected_damage(simple)) < 1.0

        # Full team baseline (Pioneer 4pc + Along the Passing Shore S1 + A4 + 开拓者盾).
        ctx = acheron_baseline_ctx()
        actual, breakdown = compute_damage(ctx)
        expected = kqm_srl_expected_damage(ctx)
        assert abs(actual - expected) / expected < 0.01, (
            f"zones diverge from KQM SRL formula: actual={actual}, formula={expected}"
        )
        assert abs(actual - KQM_TEAM_STYGIAN_EXPECTED) / KQM_TEAM_STYGIAN_EXPECTED < 0.05, (
            f"team baseline drift: actual={actual}, anchor={KQM_TEAM_STYGIAN_EXPECTED}"
        )
        assert breakdown["f_def"] == pytest.approx(100 / 215, rel=1e-4)
        assert breakdown["f_res"] == pytest.approx(1.2, rel=1e-4)

    def test_firefly_super_break(self):
        ctx = firefly_super_break_ctx()
        actual, breakdown = compute_damage(ctx)
        expected = kqm_srl_expected_damage(ctx)
        assert "f_crit" not in breakdown
        assert "f_sbBoost" in breakdown
        assert abs(actual - expected) / expected < 0.01
        assert abs(actual - KQM_FIREFLY_SUPER_BREAK_EXPECTED) / KQM_FIREFLY_SUPER_BREAK_EXPECTED < 0.05

    def test_kafka_dot_no_crit(self):
        ctx_no_crit = kafka_shock_dot_ctx(crit_rate=0.0)
        ctx_high_crit = kafka_shock_dot_ctx(crit_rate=0.95)
        dmg_no, bd_no = compute_damage(ctx_no_crit)
        dmg_hi, bd_hi = compute_damage(ctx_high_crit)
        assert "f_crit" not in bd_no
        assert "f_crit" not in bd_hi
        assert abs(dmg_no - KQM_KAFKA_SHOCK_EXPECTED) / KQM_KAFKA_SHOCK_EXPECTED < 0.05
        assert abs(dmg_hi - dmg_no) < 1e-6
