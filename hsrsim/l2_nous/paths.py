"""Pinned HSR_Nous paths. Always inject fixtures; never rely on DEFAULT_TEMPLATE_ROOTS."""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HSR_NOUS_ROOT = REPO_ROOT / "third_party" / "HSR_Nous"
NOUS_FIXTURES_TEMPLATES = HSR_NOUS_ROOT / "tests" / "fixtures" / "templates"

# Forced explicit injection (TODO v3 §0.4). Do not use data/sim_templates.
NOUS_TEMPLATE_ROOTS: tuple[Path, ...] = (NOUS_FIXTURES_TEMPLATES,)


def assert_template_under_fixtures(path: str | Path) -> Path:
    """Every resolved template path must live under tests/fixtures/templates."""
    p = Path(path).resolve()
    root = NOUS_FIXTURES_TEMPLATES.resolve()
    if not root.is_dir():
        raise FileNotFoundError(
            f"HSR_Nous fixtures missing: {root} "
            "(init submodule third_party/HSR_Nous @ 2344d045…)"
        )
    try:
        p.relative_to(root)
    except ValueError as e:
        raise AssertionError(
            f"template path must be under fixtures templates, got {p} (root={root})"
        ) from e
    return p
