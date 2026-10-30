"""Compile HSR_Nous builds with forced fixtures-only template roots."""
from __future__ import annotations

import glob
from pathlib import Path
from typing import Any

from hsrsim.l2_nous.paths import NOUS_TEMPLATE_ROOTS, assert_template_under_fixtures


def nous_template_root_strs() -> list[str]:
    return [str(p.resolve()) for p in NOUS_TEMPLATE_ROOTS]


def compile_build(build: dict[str, Any]):
    """Compile a build dict using fixtures-only roots."""
    from hsr_nous.sim.compile.build_compiler import BuildCompiler

    roots = nous_template_root_strs()
    for r in roots:
        assert_template_under_fixtures(r)
    return BuildCompiler().compile(build, template_roots=roots)


def resolve_character_template(actor_id: str) -> Path:
    """Locate character YAML under fixtures (id prefix match)."""
    roots = nous_template_root_strs()
    for root in roots:
        hits = sorted(
            h
            for h in glob.glob(f"{root}/characters/{actor_id}_*.yaml")
            if not h.endswith("_legacy.yaml")
        ) or glob.glob(f"{root}/characters/{actor_id}.yaml")
        if hits:
            if len(hits) > 1:
                raise FileNotFoundError(
                    f"ambiguous character template {actor_id!r}: {hits}"
                )
            return assert_template_under_fixtures(hits[0])
    raise FileNotFoundError(
        f"character template {actor_id!r} not under fixtures roots {roots}"
    )


def resolve_light_cone_template(lc_id: str | int) -> Path:
    """Locate light cone YAML under fixtures."""
    ref = str(lc_id)
    roots = nous_template_root_strs()
    for root in roots:
        hits = sorted(glob.glob(f"{root}/light_cones/{ref}_*.yaml")) or glob.glob(
            f"{root}/light_cones/{ref}.yaml"
        )
        if hits:
            if len(hits) > 1:
                raise FileNotFoundError(f"ambiguous LC template {ref!r}: {hits}")
            return assert_template_under_fixtures(hits[0])
    raise FileNotFoundError(f"light cone template {ref!r} not under fixtures")
