# Acheron team example

1. Official skill text: `../../data/hsr/descriptions/acheron.txt`
2. Executable character and trigger definitions:
   - `../../data/hsr/characters/acheron.json` and support files
   - `../../data/hsr/triggers/counter_gains.json`
3. Frozen L1/L2 attribution:
   - `frozen_outputs/l1_l2_source_gap_results.json` (current lock lineage)
   - `frozen_outputs/l1_l2_source_gap_results_pre_S.json` (both S1/S2 traces off)
   - Walkthrough: `../../docs/l1_l2_source_gap.md`
4. Locked calibration (S1+S2 on vs pre-S):
   - `frozen_outputs/locked_calibration_S_on.json`
   - Notes: `../../docs/locked_calibration.md`
5. Related freezes in the same folder:
   - Fribbels zone anchor, E1.5 replacement, Mortenax replace, rotation search

The trigger definitions here are hand-written formalizations of the skill text,
not model output.
