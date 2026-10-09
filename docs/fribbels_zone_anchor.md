# Fribbels per-zone anchor

Method and frozen results for comparing this repository's L2 damage zones to the Fribbels optimizer formulas under the same panel and conditionals.

Fribbels / `hsr-optimizer` source code is **not** redistributed here. The thin reader under `hsrsim/external/fribbels/` expects a local checkout at `hsr-optimizer-main/` (or `data/external/fribbels-ref/`) if you regenerate anchors. The frozen JSON below is enough to read the comparison without that checkout.

## Gate

Same-condition single action: absolute relative error `|L2 − Fribbels| / Fribbels ≤ 3%`. Zones Fribbels does not model are listed separately and excluded from the gate.

Source: `examples/e2e_acheron/frozen_outputs/acheron_fribbels_anchor.json` field `gate` = `0.03`.

## Solo skill (no teammate buffs)

From the frozen file, mode `solo`, action `skill`, MV `1.6`:

| Quantity | Value |
|---|---:|
| L2 damage | 16293.26 |
| Fribbels damage | 16623.68 |
| `|rel|` | 0.0199 |
| Pass 3% gate | true |

Per-zone factors in that record (excerpt): base, original_mult, dmg_boost, crit, … each compared with `rel_abs`; unmodeled Fribbels rows are flagged `fribbels_unmodeled`.

## How to regenerate (optional)

```bash
# requires a local Fribbels/hsr-optimizer checkout
python scripts/report_acheron_fribbels_anchor.py
```

## Notes

- This is a **result-level** evidence grade (external calculator), not live observation.
- Team-buffed modes in the same JSON use explicit conditional stacks (mirage, ashen, Sparkle CD, etc.); read the `conditionals` object before quoting a number.
