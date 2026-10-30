"""M5.5 step 1: effect target resolution (self / ally / enemy)."""
from __future__ import annotations

from hsrsim.enemies.benchmark_dummy import build_benchmark_dummy
from hsrsim.graph.loader import CHARACTERS_DIR, load_character
from hsrsim.simulator.engine import Engine
from hsrsim.simulator.types import Character, Scenario


def _dummy_enemy() -> Character:
    return build_benchmark_dummy(ally_elements=["lightning", "fire"], immortal=True)


def _acheron_f_def_after(engine: Engine, *, attacker_id: str = "acheron") -> float:
    for event in reversed(engine.events):
        if event.event_type != "damage":
            continue
        if event.payload.get("attacker") != attacker_id:
            continue
        return float(event.payload["zone_breakdown"]["f_def"])
    raise AssertionError("no damage event found")


def test_self_buff_applied_to_self():
    """Jiaoqiu skill applies ashen_roast to enemy, not self."""
    jiaoqiu = load_character(CHARACTERS_DIR / "supports" / "jiaoqiu.json")
    enemy = _dummy_enemy()
    scenario = Scenario(name="enemy-debuff", allies=[jiaoqiu], enemies=[enemy], max_rounds=1)
    engine = Engine(scenario, random_seed=0)
    actor = engine.state.find_char("jiaoqiu")
    skill = next(a for a in actor.char.build.actions if a.id == "jiaoqiu_skill")
    engine.execute_action(actor, skill, enemy.id)

    assert not any(e.id == "ashen_roast" for e in actor.active_effects)
    assert any(e.id == "ashen_roast" for e in engine.state.enemies[0].active_effects)


def test_def_shred_applied_to_enemy():
    pela = load_character(CHARACTERS_DIR / "supports" / "pela_stub.json")
    enemy = _dummy_enemy()
    scenario = Scenario(name="def-shred", allies=[pela], enemies=[enemy], max_rounds=1)
    engine = Engine(scenario, random_seed=0)
    actor = engine.state.find_char("pela_stub")
    skill = next(a for a in actor.char.build.actions if a.id == "pela_skill")
    engine.execute_action(actor, skill, enemy.id)

    assert not any(e.id == "def_shred_debuff" for e in actor.active_effects)
    assert any(e.id == "def_shred_debuff" for e in engine.state.enemies[0].active_effects)


def test_cross_character_setup_burst():
    """Support def shred on enemy must raise main DPS f_def on the follow-up hit."""
    acheron = load_character(CHARACTERS_DIR / "acheron.json")
    pela = load_character(CHARACTERS_DIR / "supports" / "pela_stub.json")
    enemy = _dummy_enemy()

    baseline = Scenario(
        name="no-setup",
        allies=[acheron],
        enemies=[enemy],
        max_rounds=1,
    )
    base_engine = Engine(baseline, random_seed=7)
    acheron_state = base_engine.state.find_char("acheron")
    basic = next(a for a in acheron_state.char.build.actions if a.id == "acheron_basic")
    base_engine.execute_action(acheron_state, basic, enemy.id)
    f_def_baseline = _acheron_f_def_after(base_engine)

    setup = Scenario(
        name="with-setup",
        allies=[pela, acheron],
        enemies=[_dummy_enemy()],
        max_rounds=1,
    )
    setup_engine = Engine(setup, random_seed=7)
    pela_state = setup_engine.state.find_char("pela_stub")
    acheron_state = setup_engine.state.find_char("acheron")
    enemy_state = setup_engine.state.enemies[0]
    pela_skill = next(a for a in pela_state.char.build.actions if a.id == "pela_skill")
    setup_engine.execute_action(pela_state, pela_skill, enemy_state.char.id)
    setup_engine.execute_action(acheron_state, basic, enemy_state.char.id)
    f_def_setup = _acheron_f_def_after(setup_engine)

    assert f_def_setup > f_def_baseline


def test_damage_reads_from_correct_source():
    acheron = load_character(CHARACTERS_DIR / "acheron.json")
    pela = load_character(CHARACTERS_DIR / "supports" / "pela_stub.json")
    enemy = _dummy_enemy()
    scenario = Scenario(name="sources", allies=[pela, acheron], enemies=[enemy], max_rounds=1)
    engine = Engine(scenario, random_seed=3)
    pela_state = engine.state.find_char("pela_stub")
    acheron_state = engine.state.find_char("acheron")
    pela_skill = next(a for a in pela_state.char.build.actions if a.id == "pela_skill")
    basic = next(a for a in acheron_state.char.build.actions if a.id == "acheron_basic")

    engine.execute_action(pela_state, pela_skill, enemy.id)
    assert any(e.id == "def_shred_debuff" for e in engine.state.enemies[0].active_effects)
    assert not any(e.id == "def_shred_debuff" for e in acheron_state.active_effects)

    engine.execute_action(acheron_state, basic, enemy.id)
    dmg_event = next(e for e in reversed(engine.events) if e.event_type == "damage")
    zones = dmg_event.payload["zone_breakdown"]
    assert zones["f_def"] > 0.5
    assert zones["f_dmgBoost"] >= 1.0
