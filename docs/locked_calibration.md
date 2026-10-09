# Locked calibration (S1 / S2)

Main public numbers for the Acheron guide-speed line use **S1 and S2 on**.

| Trace | Skill-tree id | Effect |
|---|---|---|
| S1 | 1218102 | Jiaoqiu Hearth Kindle: EHR above a floor grants ATK% (cap 240%) |
| S2 | 11306102 | Sparkle Artificial Flower: ≥3 SP spent in one ally turn → next Sparkle skill free |

Implementation: `hsrsim/rules/shared_gaps.py`.

Frozen table: `examples/e2e_acheron/frozen_outputs/locked_calibration_S_on.json`.

- `pre_S` — both traces off (historical lock before these traces were wired).
- `S_on` — both on (current lock). Difference on E1.5 zone-on lift: **+5.53% → +7.80%**.

Older L1/L2 source-gap attribution (pre-dating this S lock) is kept as
`l1_l2_source_gap_results_pre_S.json` next to the current file.
