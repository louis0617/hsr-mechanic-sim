"""M3 acceptance: compile 5 reference characters and run graph validators."""
from __future__ import annotations

from pathlib import Path

import pytest

from hsrsim.graph.compiler import HSRGraphCompiler, graph_summary
from hsrsim.catalog.loader import CHARACTERS_DIR, load_character
from hsrsim.graph.validators import (
    check_coverage,
    check_parameter_locality,
    check_trigger_reachability,
    run_all_validators,
)

M3_CHARACTERS = [
  "acheron",
  "firefly",
  "kafka",
  "dan_heng_il",
  "himeko",
]

# Expected setup → burst axis fragments (paper Definition 4.2)
EXPECTED_CHAINS: dict[str, list[tuple[str, str]]] = {
    "acheron": [("acheron_skill", "acheron_ult")],
    "firefly": [("firefly_skill", "firefly_enhanced_skill")],
    "kafka": [("kafka_skill", "kafka_talent")],
    "dan_heng_il": [("dhil_skill", "dhil_ult")],
    "himeko": [("himeko_skill", "himeko_follow_up")],
}


@pytest.fixture(scope="module")
def compiler() -> HSRGraphCompiler:
    return HSRGraphCompiler()


@pytest.mark.parametrize("char_id", M3_CHARACTERS)
def test_character_json_loads(char_id: str):
    path = CHARACTERS_DIR / f"{char_id}.json"
    assert path.exists(), f"missing {path}"
    char = load_character(path)
    assert char.id == char_id


@pytest.mark.parametrize("char_id", M3_CHARACTERS)
def test_compile_has_zone_skill_state_nodes(compiler: HSRGraphCompiler, char_id: str):
    char = load_character(CHARACTERS_DIR / f"{char_id}.json")
    G = compiler.compile(char)
    summary = graph_summary(G)
    assert summary["nodes"]["zone"] >= 10
    assert summary["nodes"]["skill"] == len(char.build.actions)
    assert summary["total_edges"] > 0


@pytest.mark.parametrize("char_id", M3_CHARACTERS)
def test_coverage(compiler: HSRGraphCompiler, char_id: str):
    char = load_character(CHARACTERS_DIR / f"{char_id}.json")
    G = compiler.compile(char)
    passed, missing = check_coverage(G)
    assert passed, f"missing zones: {missing}"


@pytest.mark.parametrize("char_id", M3_CHARACTERS)
def test_trigger_reachability(compiler: HSRGraphCompiler, char_id: str):
    char = load_character(CHARACTERS_DIR / f"{char_id}.json")
    G = compiler.compile(char)
    passed, failed = check_trigger_reachability(G, EXPECTED_CHAINS[char_id])
    assert passed, f"failed chains: {failed}"


@pytest.mark.parametrize("char_id", M3_CHARACTERS)
def test_parameter_locality(compiler: HSRGraphCompiler, char_id: str):
    char = load_character(CHARACTERS_DIR / f"{char_id}.json")
    G = compiler.compile(char)
    passed, violations = check_parameter_locality(G)
    assert passed, f"violations: {violations}"


@pytest.mark.parametrize("char_id", M3_CHARACTERS)
def test_run_all_validators(compiler: HSRGraphCompiler, char_id: str):
    char = load_character(CHARACTERS_DIR / f"{char_id}.json")
    G = compiler.compile(char)
    report = run_all_validators(G, expected_chains=EXPECTED_CHAINS[char_id])
    assert report["overall_pass"], report


def test_nested_dmg_boost_dependency_edge(compiler: HSRGraphCompiler):
    """Himeko burn uses dmg_boost.fire → dependency on f_dmgBoost."""
    char = load_character(CHARACTERS_DIR / "himeko.json")
    G = compiler.compile(char)
    deps = [
        (u, v, d)
        for u, v, d in G.out_edges("state:effect:burn", data=True)
        if d.get("type") == "dependency"
    ]
    assert ("state:effect:burn", "zone:f_dmgBoost") in [(u, v) for u, v, _ in deps]
    assert any(d.get("target_stat") == "dmg_boost.fire" for _, _, d in deps)


def test_super_break_dependency_edges(compiler: HSRGraphCompiler):
    char = load_character(CHARACTERS_DIR / "firefly.json")
    G = compiler.compile(char)
    zone_targets = {
        v
        for u, v, d in G.out_edges("state:effect:full_descent", data=True)
        if d.get("type") == "dependency"
    }
    assert "zone:f_be" in zone_targets
    assert "zone:f_sbBoost" in zone_targets


def test_visualize_smoke(tmp_path: Path):
    """Ensure visualize() can save PNG without raising."""
    pytest.importorskip("matplotlib")
    from hsrsim.graph.visualizer import visualize

    char = load_character(CHARACTERS_DIR / "acheron.json")
    G = HSRGraphCompiler().compile(char)
    out = tmp_path / "acheron_graph.png"
    fig = visualize(G, save_to=str(out), layout="shell")
    assert out.exists()
    assert out.stat().st_size > 1000
    import matplotlib.pyplot as plt

    plt.close(fig)
