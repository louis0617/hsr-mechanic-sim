# G holdout (extraction leave-out metrics)

Frozen field-accuracy table from a skill-text + datamine extraction holdout.

**Overall with-workflow field accuracy: 69.05%.**
Upgrade-to-human (missing primitive) count: 1.

| Character | Fields correct | Accuracy |
|---|---:|---:|
| jiaoqiu | 7/7 | 100% |
| pela | 6/7 | 85.71% |
| aventurine | 5/7 | 71.43% |
| sparkle | 11/21 | 52.38% |
| acheron | n/a in that run | — |

Error-type counts (with workflow): `missing_required_field` 6, `wrong_classification` 2, `wrong_value` 3, `wrong_duration_or_cap` 2, `gold_effect_missed` 1, `extra_effect` 2, `upgrade_missing_primitive` 1.

Source private report date 2026-10-06; numbers copied without re-running the LLM.
