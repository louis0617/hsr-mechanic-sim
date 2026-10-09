"""Run Fribbels↔L2 external anchor and print summary."""
from __future__ import annotations

import json
from pathlib import Path

from hsrsim.analytic.fribbels_acheron_anchor import run_external_anchor

OUT = Path(__file__).resolve().parents[1] / "docs" / "V1.0" / "ACHERON_FRIBBELS_ANCHOR.json"


def main() -> None:
    payload = run_external_anchor()
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT}")
    print("exit_pass", payload["exit_pass"])
    for mode in ("solo", "team_full_coverage"):
        block = payload[mode]
        print(f"\n=== {mode} all_pass={block['all_pass']} ===")
        for act, row in block["actions"].items():
            print(
                f"  {act}: L2={row['l2_damage']:.1f} Fri={row['fribbels_damage']:.1f} "
                f"|rel|={row['rel_abs']:.4f} pass={row['pass_gate']}"
            )
            for fr in row["fail_reasons"][:8]:
                print(f"    FAIL: {fr}")


if __name__ == "__main__":
    main()
