# P2 benchmark assets

Versioned software-engineering behavior benchmark (see `docs/uncertainty/p2-benchmark.md`).

```
issues/      30 BenchmarkIssue JSON definitions (v1)
fixtures/    pinned mini-repos (PINNED_REF per fixture)
results/     persisted JSONL runs + report JSON
```

Target balance (v1): diagnosis 5, review 5, bugfix 6, feature 5, migration 5, refactor 4.

Regenerate with `python scripts/generate_p2_benchmark.py`.
