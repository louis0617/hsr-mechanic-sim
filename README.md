# hsr-mechanic-sim

Framework for formalizing Honkai: Star Rail combat kits into executable rules, then checking them with two independent damage models.

This public tree is the **shared core**: shared triggers, L1/L2 models, axis/rotation search, and kit graphs. It is meant as a readable outline for collaborators, not a full research dump. Optional backends (e.g. HSR_Nous) are linked, not vendored.

Unofficial fan research. Not affiliated with HoYoverse / Cognosphere.

## Idea

Hand-write each kit as trigger definitions grounded in official skill text and public ParamLists. Two models consume the **same** definitions:

| Model | Role | Package |
|---|---|---|
| **L1** | Steady-state / flow model (long-run action & resource rates) | `hsrsim/analytic/` |
| **L2** | Event-driven turn simulator | `hsrsim/simulator/` |

They are built differently on purpose. Disagreement points back to a concrete source (resource rate, trigger condition, timing), which is how formalization bugs surface.

```text
official skill text + ParamLists
            │
            ▼
   trigger definitions  (data/hsr/triggers/, character JSON)
            │
     ┌──────┴──────┐
     ▼             ▼
    L1            L2
     │             │
     └──────┬──────┘
            ▼
   compare / attribute gaps
```

On top of that core:

- **Axis + rotation** — cycle-axis schemas, enumeration, and adaptive search over action patterns (`hsrsim/axis/`, `hsrsim/rotation/`).
- **Graph** — kits as heterogeneous graphs (zones / skills / state, typed edges) for structure and extraction eval (`hsrsim/graph/`).
- **Optional Nous bridge** — `hsrsim/l2_nous/` can call [HSR_Nous](https://github.com/pzc2004/HSR_Nous) if you place a checkout at `third_party/HSR_Nous`. See `third_party/README.md`. Core L1/L2 does not need it.

## Worked example: Acheron line

Data in this repo is scoped to the Acheron example line:

- DPS: Acheron, Mortenax Blade  
- Supports: Jiaoqiu, Sparkle, Aventurine, Pela, Fu Xuan (for FX teams)

Teams, loadout configs, dims YAML, and frozen example outputs live under `data/hsr/`, `configs/loadout/`, `hsrsim/rotation/dims/`, and `examples/`.

## Layout

```text
hsrsim/
  analytic/     L1 steady-state model
  simulator/    L2 event simulator
  rules/        shared SP / counter / wiring helpers
  axis/         cycle-axis schema, enum, L1/L2 eval
  rotation/     adaptive cycle search (dims + observers)
  l2_nous/      optional bridge to external HSR_Nous
  graph/        heterogeneous kit graph + loaders
  teams/        team JSON loaders
  loadout/      relic / LC static bonuses
  enemies/      benchmark dummy helpers
  llm/          optional LLM client stubs
data/hsr/       characters, triggers, teams (Acheron line)
configs/loadout/
examples/       e2e_acheron, rotation search, replacement gain, L1/L2 fingerprint
docs/           workflow, calibration notes, Fribbels / graph notes
tests/          smoke tests around the core
scripts/        datamine / report helpers
third_party/    README only — HSR_Nous not shipped
```

## Quick start

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
pytest tests/ -q
```

Useful entry points (may need local data tweaks depending on your checkout):

```bash
python examples/l1_l2_crosscheck/run_shared_rule_fingerprint.py
```

Frozen numbers and walkthroughs for the Acheron line:

| Path | Content |
|---|---|
| `examples/e2e_acheron/` | Skill text, teams, frozen L1/L2 attribution |
| `examples/rotation_search_acheron/` | Adaptive cycle search vs community axes |
| `examples/replacement_gain/` | Pela→Jiaoqiu / Mortenax replacement lifts |
| `docs/workflow.md` | Evidence grades and upgrade rules |
| `docs/locked_calibration.md` | Locked S1/S2 calibration notes |
| `docs/fribbels_zone_anchor.md` | Fribbels zone stacking gate |
| `docs/heterogeneous_graph.md` | Kit graph format |

## Design notes

- Trigger specs are **hand-written formalizations**, not model-generated kit code.
- L1 and L2 should load identical shared rules from `data/hsr/triggers/`; fingerprint helpers live under `examples/l1_l2_crosscheck/`.
- Character JSON under `data/hsr/characters/` is research formalization, not official game data.
- This public slice intentionally omits private research loops (outer/inner optimization apps, paper dumps, full character catalogs).

## License

License text to be confirmed with collaborators (MIT preferred for alignment with related public HSR tooling).

## Disclaimer

Unofficial fan research code. Not affiliated with HoYoverse or Cognosphere.
