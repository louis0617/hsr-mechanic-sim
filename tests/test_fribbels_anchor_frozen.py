# -*- coding: utf-8 -*-
"""Fribbels frozen anchor gate is documented and ≤3% for solo skill."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_fribbels_solo_skill_within_gate():
    data = json.loads(
        (ROOT / "examples" / "e2e_acheron" / "frozen_outputs" / "acheron_fribbels_anchor.json").read_text(
            encoding="utf-8"
        )
    )
    assert data["gate"] == 0.03
    skill = data["solo"]["actions"]["skill"]
    assert skill["pass_gate"] is True
    assert skill["rel_abs"] <= data["gate"]
