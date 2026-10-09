# Rotation search (Acheron line)

Adaptive cycle-axis enumeration and community-axis comparison.

## Code

- `hsrsim/rotation/` — schema, enumerate, community axes, L1/L2 eval helpers
- Frozen close-out table: `../e2e_acheron/frozen_outputs/rotation_search_results.json`
- Best axis family under S-on lock: see `locked_calibration_S_on.json` → `f_close_best_axis_family`

## Calibration (guide speed)

Sparkle 161 / support 160 / Acheron 101. L2 horizon for lock numbers: 100 cycles. Zone switch reported on/off.

Community adaptive axis id pattern (Jiaoqiu team):
`jq|ad|su1|ac1|av1|community|immediate_when_full` — `community_exact: true` in the frozen best row.

## Reproduce (lightweight)

```bash
python -c "from hsrsim.rotation.community import community_jq_axis; print(community_jq_axis().axis_id)"
pytest tests/test_rotation_smoke.py -q
```

Full adaptive enumeration is expensive; prefer the frozen JSON for paper numbers.
