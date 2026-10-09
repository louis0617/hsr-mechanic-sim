# Verification workflow

This document is the executable checklist used when formalizing a kit and comparing L1 with L2.

## How work advances

- Finish the current batch without stepwise status reports.
- Stop when an exit criterion or an **upgrade condition** is hit, then write one report.

### Upgrade conditions (stop and hand a plan to a human)

Stop if any of the following holds:

1. Evidence cannot decide a question, and the two candidate readings make **any single damage source** move by more than **3%**.
2. A mechanism looks invented.
3. A new engine primitive outside the current task is required.

## Principles

1. Do not invent mechanics or numbers. If unsure, mark `UNKNOWN` / unverified and do not write it into data.
2. **L2 is not ground truth.** Before aligning L1 to L2, check the source (skill text / ParamList / cited community sources). Every L1 approximation needs a derivation or an explicit declaration.
3. Engine implementation choices and L2 self-measurements are **not** evidence.
4. Checklist ticks must bind to evidence (test name, table, number, or report section). No evidence means fail.
5. Do not attribute L1/L2 residuals to components missing from **both** models; shared omissions do not explain a gap.
6. Shared inputs (for example Sparkle skill coverage at action time) must be labeled as shared strategy parameters, not independent predictions.
7. Role differences go through data files, not effect-name special cases in the engine.
8. Unsettled interactions become JSON switches read by both L1 and L2; report both polarities.
9. Do not change locked acceptance gates. On failure, localize one source and name the cause. Separate model error from playstyle difference.
10. Search validation, backtests, coverage measurement, and exit reconciliations use **L2 100 cycles**. Short runs may be reported separately but do not replace 100-cycle conclusions.
11. Guide-speed default for Acheron-line rotation search: Sparkle 161 / support 160 / Acheron 101.

## Evidence grades

| Grade | Meaning |
|---|---|
| Text structure | Skill / trace / light-cone / relic text + ParamList slots |
| Community consensus | ≥2 independent community sources with full citations |
| Result-level comparison | Same-condition comparison to an external calculator (state calibration differences) |
| Live observation | In-game measurement that can be reproduced independently (not an L2 score) |
| Strategy parameter | Set by the policy layer and shared by L1/L2; not mechanism evidence |
| Unverified | No citation; must not enter data |

## Self-check list (before closing a report)

### Mechanics

- Every mechanism claim has a verbatim text quote and ParamList slot, or is marked unverified.
- Every effect lists owner, target, class (buff / debuff / other), trigger event the engine can emit, window, probability, duration clock, stack cap, and damage zone.
- Evidence grade is marked; community sources are fully cited.

### Reconciliation

- Residuals use signed `(L1−L2)/L2`, with per-source rate, mean hit, contribution, and relative error.
- Gates: each source ≤3%, and `Σ|error| / team_damage ≤3%`.
- Shared inputs appear on their own rows.

### Process

- After changing shared rules, re-run the existing tests.
- Every checklist tick points at evidence.

## Worked example: Pela leave-out (E1.5)

**Question.** Replace Pela with Jiaoqiu on the Acheron guide-speed team. How much does team DPR change with zone on vs off?

**Calibration.** Frozen numbers in `examples/e2e_acheron/frozen_outputs/locked_calibration_S_on.json`:

| Switch | Zone on lift | Zone off lift |
|---|---:|---:|
| pre-S (S1/S2 off) | +5.53% | −0.53% |
| S on (S1+S2) | +7.80% | +1.73% |

**Holdout field accuracy** (skill-text + datamine extraction vs gold effects): see `docs/g_holdout.md` — with-workflow field accuracy **69.05%** overall; Pela **85.71%** fields correct on the leave-out character.

**Reproduce rotation fingerprint (no API key):**

```bash
python examples/l1_l2_crosscheck/run_shared_rule_fingerprint.py
pytest tests/test_shared_gaps_s.py::test_hearth_kindle_steps_and_cap -q
```

Full 100-cycle E1.5 numbers are frozen under `examples/e2e_acheron/frozen_outputs/` and summarized in `examples/replacement_gain/`.
