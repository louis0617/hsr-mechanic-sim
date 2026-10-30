"""E4: acheron_direct trust regression — config, zone, knot, gates, derivable."""
from __future__ import annotations

from hsrsim.analytic.acheron_derivable import (
    DREAMS_PER_ACHERON_BASIC_TURN,
    DREAMS_PER_ACHERON_SKILL_TURN,
    ULT_OVER_SKILL_MEAN_MV_RATIO_MIN,
    derive_dreams_per_turn_note,
    derive_ult_skill_mv_ratio_note,
    expected_dreams_per_acheron_turn,
)
from hsrsim.analytic.adapter import build_l1_input, refine_l1_e13
from hsrsim.analytic.jiaoqiu_zone_l1 import (
    expected_ashen_stacks,
    zone_coverage,
    zone_dream_rate,
)
from hsrsim.analytic.fribbels_acheron_anchor import REL_GATE, run_external_anchor
from hsrsim.enemies.benchmark_dummy import DUMMY_SPEED, fribbels_default_enemy_table
from hsrsim.loadout.benchmark import KEEL_PLANAR_ID, KEEL_RES_THRESHOLD
from hsrsim.rules.counter_gains import zone_proc_counts_for_dream
from hsrsim.rules.wiring import assert_l1_consumes_paper_config, config_completeness
from hsrsim.simulator.field_zone import (
    JIAOQIU_ZONE_DURATION,
    JIAOQIU_ZONE_MAX_TRIGGERS,
    JIAOQIU_ZONE_PROC_CHANCE,
    JIAOQIU_ZONE_ULT_VULN,
)
from hsrsim.simulator.ult_timing import (
    UltTimingStrategy,
    sparkle_buff_coverage_at_action,
    sparkle_buff_coverage_at_ult,
)
from hsrsim.teams.loader import load_team_spec


def test_config_completeness_complete():
    c = config_completeness(team_id="acheron_direct")
    assert c["complete"] is True
    assert c["gaps"] == []


def test_l1_consumes_paper_relics_and_zone():
    inp = build_l1_input(
        "acheron_direct",
        energy_hit=0.0,
        hits_per_100_av=1.0,
        broken_uptime=0.5,
        acheron_ult_e11h=True,
    )
    notes = assert_l1_consumes_paper_config(inp)
    assert any("relic_pioneer" in n for n in inp.notes)
    assert any("jiaoqiu_zone_ult_vuln" in n for n in inp.notes)
    assert isinstance(notes, list)


def test_zone_constants_match_paramlist_l10():
    assert JIAOQIU_ZONE_PROC_CHANCE == 0.6
    assert JIAOQIU_ZONE_ULT_VULN == 0.15
    assert JIAOQIU_ZONE_DURATION == 3
    assert JIAOQIU_ZONE_MAX_TRIGGERS == 6


def test_zone_dream_cap_and_flag():
    assert zone_proc_counts_for_dream() is True
    zd = zone_dream_rate(
        enemy_speed=DUMMY_SPEED,
        zone_cov=1.0,
        hit_chance=0.7,
        jq_ult_rate=0.4,
        counts_for_dream=True,
    )
    assert zd["rate"] <= 6.0 * 0.4 + 1e-9
    zd_f = zone_dream_rate(
        enemy_speed=DUMMY_SPEED,
        zone_cov=1.0,
        hit_chance=0.7,
        jq_ult_rate=0.4,
        counts_for_dream=False,
    )
    assert zd_f["rate"] == 0.0


def test_ashen_steady_stacks_cap():
    rates = {"nb": 0.5, "ns": 1.0, "u": 0.4}
    ash = expected_ashen_stacks(rates, zone_proc_rate=0.5, enemy_speed=DUMMY_SPEED)
    assert ash["stacks"] <= 5.0 + 1e-9
    assert abs(ash["vuln"] - (0.15 + 4 * 0.05)) < 1e-9 or ash["stacks"] < 5


def test_zone_coverage_formula():
    cov = zone_coverage({"nb": 0.2, "ns": 0.8, "u": 0.4})
    assert 0.0 <= cov <= 1.0
    assert abs(cov - min(1.0, 0.4 * 3 / 1.4)) < 1e-9


def test_ult_timing_strategy_coverage():
    assert sparkle_buff_coverage_at_ult(
        UltTimingStrategy.WAIT_SPARKLE_SKILL_BUFF, lambda_coverage=0.5
    ) == 1.0
    assert sparkle_buff_coverage_at_ult(
        UltTimingStrategy.IMMEDIATE_WHEN_FULL, lambda_coverage=0.99
    ) == 0.72
    # basic/skill also strategy-driven under immediate (E1.1h-3)
    for act in ("basic", "skill", "ult"):
        assert (
            sparkle_buff_coverage_at_action(
                UltTimingStrategy.IMMEDIATE_WHEN_FULL, act, lambda_coverage=0.99
            )
            == 0.72
        )


def test_derivable_dreams_per_acheron_turn():
    """原文推导：E2+1；战技 R3+1∧R2+1→3；普攻泡影 R2+1→2。"""
    assert DREAMS_PER_ACHERON_SKILL_TURN == 3.0
    assert DREAMS_PER_ACHERON_BASIC_TURN == 2.0
    assert expected_dreams_per_acheron_turn(1.0) == 3.0
    assert expected_dreams_per_acheron_turn(0.0) == 2.0
    assert abs(expected_dreams_per_acheron_turn(0.75) - 2.75) < 1e-9
    note = derive_dreams_per_turn_note(0.75)
    assert "E2" in note and "2.7500" in note


def test_derivable_ult_over_skill_mean():
    """E1.1e R-MV-plus-thunder-hits: (3.72+1.50)/1.60 = 3.2625 → 下限 3.26。"""
    assert ULT_OVER_SKILL_MEAN_MV_RATIO_MIN == 3.26
    assert abs((3.72 + 1.50) / 1.60 - 3.2625) < 1e-9
    note = derive_ult_skill_mv_ratio_note()
    assert "5.22" in note and "1.60" in note


def test_crimson_knot_max_9_in_character_json():
    import json
    from pathlib import Path

    path = Path("data/hsr/characters/acheron.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    vars_ = (data.get("build") or {}).get("variables") or data.get("variables") or []
    if not vars_:
        for key in ("build", "character"):
            block = data.get(key) or {}
            if "variables" in block:
                vars_ = block["variables"]
                break
    nih = next(v for v in vars_ if v.get("id") == "nihility_stacks")
    assert float(nih["max_value"]) == 9.0


def test_fribbels_dummy_unchanged():
    table = fribbels_default_enemy_table()
    assert "fields" in table
    assert table["fields"]["level"]["ours"] == 95
    assert DUMMY_SPEED == 90.0
    assert table["fields"]["speed"]["ours"] == DUMMY_SPEED


def test_fribbels_external_anchor_same_conditions():
    """同条件单人/全队外部锚点：单次伤害 |rel|≤3%；不建模项不计入门槛。"""
    out = run_external_anchor()
    assert out["gate"] == REL_GATE
    assert out["exit_pass"] is True
    assert out["solo"]["all_pass"] is True
    assert out["team_full_coverage"]["all_pass"] is True
    for mode in ("solo", "team_full_coverage"):
        for act, row in out[mode]["actions"].items():
            assert row["pass_gate"] is True, (mode, act, row.get("fail_reasons"))
            assert row["rel_abs"] <= REL_GATE + 1e-12
            assert not row["fail_reasons"]
    assert {u["id"] for u in out["fribbels_unmodeled"]} >= {
        "zone_proc_dream",
        "quad_ascendance",
        "mirage_hit_timing",
    }
    vsu = out["team_full_coverage"]["vuln_skill_vs_ult"]
    assert vsu["delta_matches_zone"] is True
    assert abs(vsu["delta"] - 0.15) < 1e-9
    vc = out["team_full_coverage"]["vuln_composition"]
    assert abs(vc["skill"]["sparkle_figment_aura"] - vc["ult"]["sparkle_figment_aura"]) < 1e-12
    assert abs(vc["ult"]["jiaoqiu_zone_ult"] - 0.15) < 1e-12
    assert abs(vc["skill"]["jiaoqiu_zone_ult"]) < 1e-12


def test_broken_keel_wearers_meet_res_threshold():
    """配装生成器应满足 Broken Keel (b) 条件 RES≥30%。"""
    spec = load_team_spec("acheron_direct")
    for cid in ("sparkle", "aventurine"):
        ch = next(c for c in spec.scenario.allies if c.id == cid)
        assert float(ch.build.stats.effect_res) + 1e-9 >= KEEL_RES_THRESHOLD, (
            f"{cid} RES={ch.build.stats.effect_res} < {KEEL_RES_THRESHOLD} "
            f"(planar={KEEL_PLANAR_ID})"
        )


def test_refine_l1_e13_splits_sparkle_per_action():
    inp = build_l1_input(
        "acheron_direct",
        energy_hit=0.0,
        hits_per_100_av=1.0,
        broken_uptime=0.5,
        acheron_ult_e11h=True,
    )
    rates = {
        "jiaoqiu": {"nb": 0.4, "ns": 1.0, "u": 0.35},
        "acheron": {"nb": 0.3, "ns": 1.0, "u": 0.7},
        "sparkle": {"nb": 0.5, "ns": 1.0, "u": 0.35},
        "aventurine": {"nb": 0.4, "ns": 0.5, "u": 0.3},
    }
    out, meta = refine_l1_e13(
        inp,
        rates,
        enemy_speed=DUMMY_SPEED,
        enemy_effect_res=0.3,
        zone_proc_counts=True,
        fua_dream_external=0.3,
        ult_timing=UltTimingStrategy.IMMEDIATE_WHEN_FULL.value,
    )
    ctr = next(c for c in out.counters if c.name == "nihility_stacks")
    assert float(ctr.external_gain) >= 0.3
    assert meta["zone_cov"] > 0
    assert meta["ashen"]["stacks"] > 0
    assert meta["sparkle_at_basic_cov"] == 0.72
    assert meta["sparkle_at_skill_cov"] == 0.72
    assert meta["sparkle_at_ult_cov"] == 0.72
    sparkle_splits = [b for b in out.buffs if "sparkle_skill_buff" in str(b.id)]
    assert any(b.applies_to == frozenset({"basic"}) for b in sparkle_splits)
    assert any(b.applies_to == frozenset({"skill"}) for b in sparkle_splits)
    assert any(b.applies_to == frozenset({"ult"}) for b in sparkle_splits)
