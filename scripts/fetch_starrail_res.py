# -*- coding: utf-8 -*-
"""Clone Mar-7th/StarRailRes into data/external/StarRailRes (not redistributed)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TARGET = REPO / "data" / "external" / "StarRailRes"
URL = "https://github.com/Mar-7th/StarRailRes.git"


def main() -> int:
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    if TARGET.exists():
        print(f"already exists: {TARGET}")
        return 0
    cmd = ["git", "clone", "--depth", "1", URL, str(TARGET)]
    print(" ".join(cmd))
    subprocess.check_call(cmd)
    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
