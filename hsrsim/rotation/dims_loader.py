"""Load per-character decision_dims + cycle_signature_fields."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

DIMS_DIR = Path(__file__).resolve().parent / "dims"

_SLUG_FILES = {
    "acheron": "1308_acheron.yaml",
    "sparkle": "1306_sparkle.yaml",
    "jiaoqiu": "1218_jiaoqiu.yaml",
    "pela": "1106_pela.yaml",
    "aventurine": "1304_aventurine.yaml",
    "mortenaxblade": "1220_mortenaxblade.yaml",
}


def _normalize_field(field: dict) -> dict:
    """YAML 1.1 parses the key ``on`` as boolean True."""
    item = dict(field)
    if "on" not in item and True in item:
        item["on"] = item.pop(True)
    return item


def load_character_dims(slug: str) -> dict[str, Any]:
    name = _SLUG_FILES.get(slug)
    if not name:
        raise KeyError(f"no dims file for slug={slug!r}")
    path = DIMS_DIR / name
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"bad dims yaml: {path}")
    fields = data.get("cycle_signature_fields")
    if isinstance(fields, list):
        data["cycle_signature_fields"] = [
            _normalize_field(field) if isinstance(field, dict) else field for field in fields
        ]
    return data


def load_team_signature_fields(slugs: list[str]) -> list[dict[str, Any]]:
    """Union of cycle_signature_fields.

    ``on: self`` stays per character. Team-wide fields (skill points, enemy
    stacks) are deduped. Each row carries the declaring ``character`` slug.
    """
    seen: set[tuple] = set()
    out: list[dict[str, Any]] = []
    for slug in slugs:
        for field in load_character_dims(slug).get("cycle_signature_fields") or []:
            item = dict(field)
            item["character"] = slug
            on = item.get("on")
            key = (
                slug if on == "self" else None,
                item.get("kind"),
                item.get("id"),
                on,
                item.get("name"),
            )
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
    return out


def decision_space_size(slugs: list[str]) -> int:
    n = 1
    for slug in slugs:
        for dim in load_character_dims(slug).get("decision_dims") or []:
            n *= max(1, len(dim.get("values") or []))
    return n
