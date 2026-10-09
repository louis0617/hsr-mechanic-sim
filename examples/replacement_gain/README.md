# Replacement gain examples

## E1.5 — Pela → Jiaoqiu

Guide-speed Acheron team, adaptive community axis, zone on/off.

| Calibration | Zone on lift | Zone off lift | JQ DPR (zone on) | Pela DPR |
|---|---:|---:|---:|---:|
| pre-S | +5.53% | −0.53% | 720504.4 | 682754.2 |
| S1+S2 on | +7.80% | +1.73% | 735999.7 | 682754.2 |

Source: `../e2e_acheron/frozen_outputs/locked_calibration_S_on.json`.
Core / backtest dumps: `e15_replacement_core_results.json`, `e15_backtest_results.json`.

## Mortenax Blade replaces Jiaoqiu

| Calibration | n1 dream | n3 dream | n1 no-dream |
|---|---:|---:|---:|
| pre-S | +38.15% | +34.93% | +32.21% |

(S-on enum summaries live in the same locked JSON under private archival copies; quote pre-S here as the published leave-out lock unless regenerating.)

Raw dump: `../e2e_acheron/frozen_outputs/mortenax_replace_results.json`.

## Data files

- Supports: `data/hsr/characters/supports/pela.json`, `jiaoqiu.json`
- Loadouts: `configs/loadout/acheron_old_pela_*.yaml`, `acheron_mortenax*.yaml` (when present)
