# Third-party dependencies

## HSR_Nous (optional, not vendored)

`hsrsim/l2_nous/` and adaptive rotation compile paths can call into [HSR_Nous](https://github.com/pzc2004/HSR_Nous) (MIT) as an external simulator backend.

This public tree does **not** ship HSR_Nous. Place a checkout under `third_party/HSR_Nous` only if you need those paths; L1/L2 core, shared rules, graph, and axis code do not require it.

Expected layout when present:

```text
third_party/HSR_Nous/
  LICENSE
  src/
  tests/fixtures/templates/
```
