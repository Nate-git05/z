# Reference aggregates (scripted adapter)

Full 30-issue run on the P2 landing commit
(`python -m aider.z.benchmark run`):

| Metric | Full | Baseline | Δ |
|--------|------|----------|---|
| Issue resolution | 100% | 53.3% | +46.7pp |
| False completion | 0% | 46.7% | −46.7pp |
| Unnecessary edit rate | 0% | 33.3% | −33.3pp |
| Unnecessary planning (diag/review) | 10% | 100% | −90pp |
| Avg approval interruptions | 0.0 | 1.43 | −1.43 |
| Avg time blocked (s) | 0.0 | 0.72 | −0.72 |

Baseline = same issues with the uncertainty/planning layer disabled
(`Z_UNCERTAINTY_DISABLED` / harness flag). This is the standing
full-vs-none comparison; a historic pre-P0 git tag can be layered on later.

Raw `run-*.jsonl` / `run-*.report.json` files are gitignored — re-run locally
or in CI and archive artifacts outside the repo if you need history.
