"""E1.5 leave-out tests: Pela from unpack ParamList + workflow checklist only."""
from __future__ import annotations

import pytest

from hsrsim.catalog.loader import load_character_by_id
from hsrsim.rules.wiring import WIRED_EIDOLON_MAX, lc_param_list
from hsrsim.simulator.bots import GreedyBot
from hsrsim.simulator.combat_rules import CombatRules
from hsrsim.simulator.engine import Engine
from hsrsim.simulator.triggers import LC_EXPOSED_ID
from hsrsim.simulator.types import Scenario
from hsrsim.enemies.benchmark_dummy import build_benchmark_dummy
from hsrsim.teams.loader import load_team_spec


def test_pela_ult_def_shred_not_on_skill():
    """Checklist catch: stub wrongly put DEF shred on skill; unpack puts it on ult."""
    pela = load_character_by_id("pela")
    skill = next(a for a in pela.build.actions if a.id == "pela_skill")
    ult = next(a for a in pela.build.actions if a.id == "pela_ult")
    assert "pela_ult_def_shred" not in (skill.applies_effects or [])
    assert "pela_ult_def_shred" in (ult.applies_effects or [])
    shred = next(e for e in pela.build.effects if e.id == "pela_ult_def_shred")
    assert shred.target.value == "all_enemies"
    assert shred.duration_turns == 2
    mod = next(m for m in shred.modifiers if m.target_stat == "def_reduction")
    # E6 → E5 SkillAdd ult+2 → L12 Param#2 = 0.42
    assert abs(float(mod.value) - 0.42) < 1e-9


def test_pela_e4_ice_res_on_skill_paramlist():
    pela = load_character_by_id("pela")
    skill = next(a for a in pela.build.actions if a.id == "pela_skill")
    assert "pela_e4_ice_res_shred" in (skill.applies_effects or [])
    eff = next(e for e in pela.build.effects if e.id == "pela_e4_ice_res_shred")
    mod = next(m for m in eff.modifiers if m.target_stat == "res")
    assert abs(float(mod.value) - (-0.12)) < 1e-9
    assert eff.duration_turns == 2


def test_pela_skill_levels_e6_skill_add():
    """E3: skill+2/basic+1；E5: ult+2/talent+2 → ParamList L7/L12/L12 multipliers."""
    pela = load_character_by_id("pela")
    ba = next(a for a in pela.build.actions if a.id == "pela_basic")
    sk = next(a for a in pela.build.actions if a.id == "pela_skill")
    ult = next(a for a in pela.build.actions if a.id == "pela_ult")
    assert abs(ba.damage_instances[0].multiplier - 1.1) < 1e-9
    assert abs(sk.damage_instances[0].multiplier - 2.31) < 1e-9
    assert abs(ult.damage_instances[0].multiplier - 1.08) < 1e-9
    assert ba.skill_id == 110601
    assert sk.skill_id == 110602
    assert ult.skill_id == 110603


def test_pela_ult_applies_def_shred_and_counts_r1_path():
    """Ult applies 通解; effect_applied on enemy → R1 dream path eligible."""
    pela = load_character_by_id("pela")
    pela = pela.model_copy(update={"eidolon": 6, "light_cone_id": 21015, "light_cone_superimposition": 5})
    enemy = build_benchmark_dummy(ally_elements=["ice"], immortal=True)
    sc = Scenario(name="pela_ult", allies=[pela], enemies=[enemy], max_cycles=1)
    eng = Engine(sc, ally_bot=GreedyBot(), random_seed=1, combat_rules=CombatRules())
    actor = eng.state.find_char("pela")
    assert actor is not None
    ult = next(a for a in actor.char.build.actions if a.id == "pela_ult")
    eng.execute_inserted_action(actor, ult, enemy.id)
    effs = [e for e in eng.state.enemies[0].active_effects if e.id == "pela_ult_def_shred"]
    assert effs, "通解 missing after ult"
    assert any(
        ev.event_type == "effect_applied" and ev.payload.get("effect_id") == "pela_ult_def_shred"
        for ev in eng.events
    )


def test_lc_21015_exposed_params_s5():
    p = lc_param_list(21015, 5)
    assert abs(p[0] - 1.0) < 1e-9
    assert abs(p[1] - 0.16) < 1e-9
    assert int(p[2]) == 1


def test_old_team_load_wiring_incomplete_marked():
    """Pela paper teams complete after EHR/E6 wired; FX keeps Fu Xuan stub gap."""
    from hsrsim.rules.wiring import PELA_CLOSED_GAPS, TEAM_CONFIG_GAPS, config_completeness

    assert WIRED_EIDOLON_MAX.get("pela") == 6
    assert PELA_CLOSED_GAPS
    for tid in ("acheron_old_pela_res", "acheron_old_pela_tut"):
        spec = load_team_spec(tid)
        assert spec.eidolons["pela"] == 6
        pela = next(c for c in spec.scenario.allies if c.id == "pela")
        assert pela.light_cone_id in (21015, 22000)
        c = config_completeness(team_id=tid)
        assert c["complete"] is True
        assert c["gaps"] == []
        assert not (c["complete"] is True and c["gaps"])
    fx = config_completeness(team_id="acheron_old_pela_fx_res")
    assert fx["complete"] is False
    assert any("符玄" in g for g in fx["gaps"])
    assert "acheron_old_pela_fx_res" in TEAM_CONFIG_GAPS


def test_pela_e6_extra_requires_defender_debuff():
    pela = load_character_by_id("pela")
    for aid in ("pela_basic", "pela_skill", "pela_ult"):
        act = next(a for a in pela.build.actions if a.id == aid)
        extras = [d for d in act.damage_instances if abs(d.multiplier - 0.4) < 1e-9]
        assert extras, aid
        assert all(d.requires_defender_debuff for d in extras)


def test_pela_team_ehr_applied_at_battle_start():
    spec = load_team_spec("acheron_old_pela_res")
    sc = spec.scenario.model_copy(deep=True)
    sc.max_cycles = 1
    eng = Engine(
        sc,
        ally_bot=GreedyBot(),
        enemy_bot=GreedyBot(),
        random_seed=1,
        combat_rules=CombatRules(),
    )
    eng.bus.emit("battle_start", {})
    ach = eng.state.find_char("acheron")
    assert ach is not None
    assert any(e.id == "pela_trace_team_ehr" for e in ach.active_effects)
    assert float(ach.char.build.stats.ehr) >= 0.10 - 1e-9


def test_fx_variant_loads_fu_xuan_trend():
    from hsrsim.rules.wiring import lc_param_list

    p = lc_param_list(21016, 5)
    assert abs(p[0] - 0.32) < 1e-9
    assert abs(p[1] - 1.20) < 1e-9
    for tid in ("acheron_old_pela_fx_res", "acheron_jq_fx"):
        spec = load_team_spec(tid)
        fx = next(c for c in spec.scenario.allies if c.id == "fu_xuan")
        assert fx.light_cone_id == 21016
        assert fx.path.value == "preservation"


def test_trend_burn_on_hit_before_resolved_counts_r1():
    """21016 hit → enemy burn while action_ctx alive → R1-eligible effect_applied."""
    from hsrsim.simulator.triggers import LC_TREND_BURN_ID

    fu = load_character_by_id("fu_xuan")
    fu = fu.model_copy(
        update={"light_cone_id": 21016, "light_cone_superimposition": 5}
    )
    # Solo-ally so BaseAggro always picks Trend wearer; ER=0 → S5 chance lands.
    enemy = build_benchmark_dummy(ally_elements=["quantum"], immortal=True)
    enemy.build.stats.effect_res = 0.0
    sc = Scenario(
        name="trend_r1",
        allies=[fu],
        enemies=[enemy],
        max_cycles=2,
    )
    eng = Engine(
        sc,
        ally_bot=GreedyBot(),
        enemy_bot=GreedyBot(),
        random_seed=1,
        combat_rules=CombatRules(hits_per_enemy_action=2),
    )
    en = eng.state.enemies[0]
    act = next(a for a in en.char.build.actions if a.type.value == "basic_attack")
    eng.execute_inserted_action(en, act, fu.id)
    burns = [
        e
        for e in eng.events
        if e.event_type == "effect_applied" and e.payload.get("effect_id") == LC_TREND_BURN_ID
    ]
    assert burns, "Trend burn missing after ally_hit"
    assert any(e.payload.get("target_side") == "enemy" for e in burns)
    assert any(e.id == LC_TREND_BURN_ID for e in eng.state.enemies[0].active_effects)


def test_21015_skip_if_already_ensnared():
    """TextMapEN: only if not already Ensnared — no refresh roll."""
    spec = load_team_spec("acheron_old_pela_res")
    pela = next(c for c in spec.scenario.allies if c.id == "pela")
    dummy = spec.scenario.enemies[0]
    dummy.build.stats.effect_res = 0.0
    sc = spec.scenario.model_copy(deep=True)
    sc.allies = [pela]
    sc.enemies = [dummy]
    sc.max_cycles = 4
    eng = Engine(
        sc,
        ally_bot=GreedyBot(),
        enemy_bot=GreedyBot(),
        random_seed=1,
        combat_rules=CombatRules(),
    )
    eng.run()
    applies = [
        e
        for e in eng.events
        if e.event_type == "effect_applied"
        and e.payload.get("effect_id") == LC_EXPOSED_ID
    ]
    assert applies
    assert all(not e.payload.get("was_refresh") for e in applies)


def test_n_enemies_explicit_dummies():
    spec = load_team_spec("acheron_old_pela_res", n_enemies=3)
    ids = [e.id for e in spec.scenario.enemies]
    assert len(ids) == 3
    assert len(set(ids)) == 3


def test_pela_talent_energy_from_table_not_hardcoded():
    from hsrsim.rules.pela_talent import talent_extra_energy

    assert talent_extra_energy(12) == 11.0
    assert talent_extra_energy(1) == 5.0
    with pytest.raises(KeyError):
        talent_extra_energy(99)


def test_spbase_missing_key_raises():
    from hsrsim.simulator.energy import spbase_for_skill_id

    with pytest.raises(KeyError):
        spbase_for_skill_id(None)
    with pytest.raises(KeyError):
        spbase_for_skill_id(999999)
