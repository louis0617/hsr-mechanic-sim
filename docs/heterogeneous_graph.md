# Heterogeneous skill graph

Characters can be compiled into a typed graph for structure checks and extraction evaluation. Combat L1/L2 do **not** require this package.

## Modules

| Path | Role |
|---|---|
| `hsrsim/graph/compiler.py` | Compile a `Character` JSON into nodes/edges |
| `hsrsim/graph/extractor.py` | LLM extraction from skill text (needs API key) |
| `hsrsim/graph/validators.py` | Structural validators |
| `hsrsim/catalog/loader.py` | Load gold / formalized character JSON |

## Frozen extraction evaluation (no API key)

Five characters were extracted once and frozen under `results/extractions/`. Scoring uses node-id alignment after matching skills/effects/variables.

From `results/eval_extraction_checkpoint.json` summary:

| Metric | Value |
|---|---:|
| Characters | 5 |
| Mean node F1 | 0.749 |
| Mean **aligned edge F1** | **0.705** |

**Calibration.** Aligned edge F1 after node-id matching (not raw edge F1). Provider metadata in the checkpoint records DeepSeek was used for the original extraction run; re-scoring the frozen files does not call the API.

```bash
python scripts/eval_extraction.py --from-checkpoint results/eval_extraction_checkpoint.json
# or score frozen files against gold characters under data/hsr/characters/
```

If the eval script's CLI flags differ in your checkout, open `scripts/eval_extraction.py` and use the documented entry point; the checkpoint JSON is the authoritative frozen score table.

## Example figure (Acheron)

Frozen render: [`docs/figures/acheron_kit_graph.png`](figures/acheron_kit_graph.png) (DOT twin: `acheron_kit_graph.dot`). Regenerate:

```bash
PYTHONPATH=. python scripts/_gen_acheron_graph_figure.py
```

```python
from hsrsim.catalog.loader import load_character_by_id
from hsrsim.graph.compiler import HSRGraphCompiler

char = load_character_by_id("acheron")
g = HSRGraphCompiler().compile(char)
print(g.number_of_nodes(), g.number_of_edges())
```

Visualizer: `hsrsim/graph/visualizer.py` (needs matplotlib).
