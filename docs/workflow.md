# Formalizing a kit

1. Read the official skill text (for example from StarRailRes descriptions).
2. Encode executable definitions:
   - Character actions and effects as `Character` JSON (`hsrsim/simulator/types.py`).
   - Shared counter and hook rules as descriptors under `data/hsr/triggers/`.
3. Run L1 (`hsrsim/analytic/`) and L2 (`hsrsim/simulator/`) on those definitions.
4. Compare rule fingerprints, then attribute remaining damage gaps by source.

Event schema: [`event_triggers.md`](event_triggers.md). Shared counter-gain and skill-point rules: [`shared_trigger_rules.md`](shared_trigger_rules.md).
