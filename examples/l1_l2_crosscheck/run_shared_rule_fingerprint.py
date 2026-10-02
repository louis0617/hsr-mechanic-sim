# -*- coding: utf-8 -*-
"""Minimal L1↔L2 shared counter-gain fingerprint check (Acheron line)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from hsrsim.enemies.benchmark_dummy import build_benchmark_dummy
from hsrsim.catalog.loader import load_character_by_id
from hsrsim.rules.counter_gains import l1_l2_rule_fingerprint, specs_for_resource
from hsrsim.simulator.engine import Engine
from hsrsim.simulator.types import Scenario


def main() -> None:
    specs = specs_for_resource("nihility_stacks")
    l2_fp = l1_l2_rule_fingerprint(specs)
    allies = [
        load_character_by_id(cid)
        for cid in ("acheron", "jiaoqiu", "sparkle", "aventurine")
    ]
    allies = [
        a.model_copy(
            update={
                "eidolon": 2 if a.id == "acheron" else 0,
                "light_cone_id": 23024 if a.id == "acheron" else a.light_cone_id,
            }
        )
        for a in allies
    ]
    enemy = build_benchmark_dummy(ally_elements=["lightning"])
    engine = Engine(
        Scenario(name="shared-rules", allies=allies, enemies=[enemy], max_rounds=1),
        random_seed=0,
    )
    registered = engine.bus.registered_counter_gain_fingerprint("nihility_stacks")
    print("l2_fp=", l2_fp)
    print("registered=", registered)
    print("match=", registered == l2_fp)
    if registered != l2_fp:
        raise SystemExit(1)
    print("ok")


if __name__ == "__main__":
    main()
