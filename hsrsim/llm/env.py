"""Load project .env into os.environ (stdlib only)."""
from __future__ import annotations

import os
from pathlib import Path


def _parse_env_line(line: str) -> tuple[str, str] | None:
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    if "=" not in line:
        return None
    key, _, value = line.partition("=")
    key = key.strip()
    value = value.strip().strip("'").strip('"')
    if not key:
        return None
    return key, value


def find_dotenv(start: Path | None = None) -> Path | None:
    """Walk up from *start* (default cwd) looking for a `.env` file."""
    base = (start or Path.cwd()).resolve()
    for directory in [base, *base.parents]:
        candidate = directory / ".env"
        if candidate.is_file():
            return candidate
    return None


def load_dotenv(path: Path | None = None, *, override: bool = False) -> Path | None:
    """Load `.env` into ``os.environ``. Returns the file path if loaded."""
    env_path = path or find_dotenv()
    if env_path is None:
        return None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        parsed = _parse_env_line(line)
        if parsed is None:
            continue
        key, value = parsed
        if override or key not in os.environ:
            os.environ[key] = value
    return env_path
