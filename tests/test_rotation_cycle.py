"""Cycle signature, successive halving, and coordinate descent. No 100-round search."""
from types import SimpleNamespace

from hsrsim.rotation.cycle import CycleObserver, format_cycle, read_signature
from hsrsim.rotation.dims_loader import load_team_signature_fields
from hsrsim.rotation.search import coordinate_descent, successive_halving


def _unit(slug, *, energy=0.0, variables=None, effects=None):
    return SimpleNamespace(
        char=SimpleNamespace(id=slug),
        energy_current=energy,
        variables=dict(variables or {}),
        active_effects=list(effects or []),
        is_alive=True,
    )


def _effect(effect_id, stacks):
    return SimpleNamespace(id=effect_id, current_stacks=stacks)


def test_signature_fields_keep_each_characters_energy():
    fields = load_team_signature_fields(["acheron", "sparkle", "jiaoqiu", "aventurine"])
    energy = [row for row in fields if row["kind"] == "energy"]
    assert {row["character"] for row in energy} == {
        "acheron",
        "sparkle",
        "jiaoqiu",
        "aventurine",
    }
    points = [row for row in fields if row["kind"] == "skill_points"]
    assert len(points) == 1


def test_read_signature_uses_runtime_ids_and_marks_missing():
    acheron = _unit("acheron", energy=40.2, variables={"nihility_stacks": 3})
    enemy = _unit("e1", effects=[_effect("crimson_knot", 2)])
    state = SimpleNamespace(
        allies=[acheron],
        enemies=[enemy],
        sp_team_pool=4,
    )
    fields = [
        {"character": "acheron", "kind": "resource", "id": "nihility_stacks", "on": "self"},
        {"character": "acheron", "kind": "energy", "on": "self"},
        {"character": "acheron", "kind": "modifier_stacks", "id": "crimson_knot", "on": "enemies_max"},
        {"character": "acheron", "kind": "modifier_stacks", "id": "ULT_KNOT_LOCK", "on": "self"},
        {"kind": "skill_points", "on": "team"},
    ]
    signature, unresolved = read_signature(state, fields, actor_id="acheron")
    values = [value for _, value in signature]
    assert values[:4] == [3, 40, 2, None]
    assert values[4] == 4
    assert any("ULT_KNOT_LOCK" in item for item in unresolved)


def test_dot_remaining_turns_reads_duration_on_dot_instance():
    enemy = _unit(
        "e1",
        effects=[
            SimpleNamespace(id="ashen_roast", current_stacks=3, duration_turns=2, dot_instance=object()),
            SimpleNamespace(id="ashen_roast", current_stacks=1, duration_turns=9, dot_instance=None),
        ],
    )
    state = SimpleNamespace(allies=[], enemies=[enemy], sp_team_pool=0)
    fields = [
        {
            "character": "jiaoqiu",
            "kind": "dot_remaining_turns",
            "id": "ashen_roast",
            "on": "enemies_max",
        }
    ]
    signature, unresolved = read_signature(state, fields, actor_id="acheron")
    assert signature[0][1] == 2
    assert unresolved == []


def test_observer_closes_cycle_on_repeated_signature():
    fields = [{"character": "acheron", "kind": "energy", "on": "self"}]
    observer = CycleObserver(fields, anchor_id="acheron")
    acheron = _unit("acheron", energy=10)
    state = SimpleNamespace(allies=[acheron], enemies=[], sp_team_pool=3, av_clock=0.0, total_damage_dealt=0.0)
    engine = SimpleNamespace(state=state)

    observer.on_turn_start(engine, {"unit_id": "acheron"})
    state.av_clock = 50.0
    state.total_damage_dealt = 100.0
    acheron.energy_current = 80
    observer.on_turn_start(engine, {"unit_id": "acheron"})
    state.av_clock = 150.0
    state.total_damage_dealt = 400.0
    acheron.energy_current = 10
    observer.on_turn_start(engine, {"unit_id": "acheron"})

    result = observer.result()
    assert result["converged"] is True
    assert result["cycle_av"] == 150.0
    assert result["cycle_damage"] == 400.0
    assert result["cycle_dpr"] == 400.0 * (100.0 / 150.0)


def test_format_cycle_uses_action_kind():
    text = format_cycle(
        [
            {"actor_id": "sparkle", "kind": "skill"},
            {"actor_id": "acheron", "kind": "basic_attack"},
            {"actor_id": "acheron", "kind": "ultimate"},
        ]
    )
    assert text == "花火战技 → 黄泉普攻 → 黄泉终结技"


def test_successive_halving_keeps_the_declared_fractions():
    calls = []

    def evaluate(item, budget):
        calls.append((item, budget))
        return float(item)

    out = successive_halving(list(range(10)), evaluate)
    assert [row["n"] for row in out["history"]] == [10, 2, 2]
    assert [row["budget"] for row in out["history"]] == [20, 50, 100]
    assert out["survivors"] == [9, 8]
    assert calls[0] == (0, 20)
    assert calls[-1] == (8, 100)


def test_coordinate_descent_stops_when_a_pass_does_not_improve():
    dims = [
        {"name": "x", "values": [0, 1, 2]},
        {"name": "y", "values": [0, 1, 2]},
    ]

    def evaluate(point):
        return -((point["x"] - 1) ** 2 + (point["y"] - 2) ** 2)

    out = coordinate_descent({"x": 0, "y": 0}, dims, evaluate)
    assert out["point"] == {"x": 1, "y": 2}
    assert out["score"] == 0
    assert out["passes"] >= 2
