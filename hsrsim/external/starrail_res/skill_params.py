"""Resolve StarRailRes skill description placeholders against params[]."""
from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

# #2[i]  /  #3[f1]  — N is 1-based index into the level's params row
_PLACEHOLDER_RE = re.compile(r"#(\d+)\[(i|f\d+)\]")


def _row_for_level(
    params: Sequence[Any],
    level: int,
) -> Sequence[float]:
    """Select params row. ``level`` is 1-based (game skill level)."""
    if level < 1:
        raise ValueError(f"level must be >= 1 (1-based), got {level}")
    if not params:
        raise ValueError("params is empty")
    first = params[0]
    # Single row: [2, 4, 0.06, 2]
    if not isinstance(first, (list, tuple)):
        if level != 1:
            raise ValueError(
                f"single-row params only valid for level=1, got level={level}"
            )
        return params  # type: ignore[return-value]
    idx = level - 1
    if idx >= len(params):
        raise ValueError(
            f"level {level} out of range (params has {len(params)} levels)"
        )
    return params[idx]


def _display_value(raw: float, fmt: str, as_percent: bool) -> int | float:
    scaled = raw * 100.0 if as_percent else raw
    if fmt == "i":
        return int(round(scaled))
    if fmt.startswith("f") and fmt[1:].isdigit():
        digits = int(fmt[1:])
        return round(scaled, digits)
    raise ValueError(f"unsupported format specifier: {fmt!r}")


def resolve_skill_param(
    desc: str,
    params: Sequence[Any],
    level: int,
) -> dict[str, dict[str, Any]]:
    """Map each ``#N[i|fK]`` in ``desc`` to values from ``params`` at ``level``.

    Rules (StarRailRes / in-game text):
    - ``#N[...]`` uses the N-th entry of the level row (**1-based**).
    - ``[i]`` → integer display; ``[f1]`` / ``[f2]`` → that many decimal places.
    - If the placeholder is immediately followed by ``%`` in ``desc``, the
      display ``value`` is ``raw * 100`` (percent text), otherwise ``raw``.

    ``level`` is **1-based** (level 1 → ``params[0]``). ``params`` may be either
    a full level matrix ``list[list[float]]`` or a single row.

    Returns:
        Dict keyed by the placeholder token (e.g. ``\"#2[i]\"``), each value::

            {
              \"index\": int,       # 1-based
              \"raw\": float,       # from params
              \"format\": str,      # \"i\" | \"f1\" | ...
              \"as_percent\": bool,
              \"value\": int|float, # display-oriented
            }
    """
    row = _row_for_level(params, level)
    out: dict[str, dict[str, Any]] = {}
    for m in _PLACEHOLDER_RE.finditer(desc):
        token = m.group(0)
        n = int(m.group(1))
        fmt = m.group(2)
        if n < 1 or n > len(row):
            raise ValueError(
                f"{token}: index {n} out of range for params row len={len(row)}"
            )
        raw = float(row[n - 1])
        as_percent = desc[m.end() : m.end() + 1] == "%"
        out[token] = {
            "index": n,
            "raw": raw,
            "format": fmt,
            "as_percent": as_percent,
            "value": _display_value(raw, fmt, as_percent),
        }
    return out


def format_resolved_desc(
    desc: str,
    resolved: Mapping[str, Mapping[str, Any]],
) -> str:
    """Optional helper: substitute placeholders with display values (tests/debug)."""

    def repl(m: re.Match[str]) -> str:
        token = m.group(0)
        info = resolved.get(token)
        if info is None:
            return token
        text = str(info["value"])
        if info.get("as_percent"):
            return text  # caller still sees trailing % from original desc
        return text

    return _PLACEHOLDER_RE.sub(repl, desc)
