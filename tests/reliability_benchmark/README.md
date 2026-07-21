# Reliability benchmark fixtures (stubs)

These are **lightweight stubs** for the taxonomy in
`aider/z/uncertainty/benchmark.py`. Full interactive agent evals are run
separately; CI uses `score_task` / `aggregate_false_completion_rate`.

## Categories

| Id | Category |
|----|----------|
| new_web_app | New web applications |
| existing_feature | Existing-codebase feature additions |
| concurrency | Backend concurrency |
| migration | Database migrations |
| auth_change | Authentication changes |
| misleading_bug | Bug diagnosis with misleading symptoms |
| dep_failure | Dependency failures |
| wrong_tests | Broken tests (production correct) |
| wrong_prod | Broken production (tests wrong) |
| multi_user | Multi-user / real-time |
| process_instructions | Process + product requirements |
| stop_and_ask | Correct outcome is stop and ask |

## Primary metric

```
false_completion_rate = count(claimed_complete ∧ ¬required_evidence) / N
```

Secondary: verification-weakening rate, correct evidence-type resolution rate,
reopened-node rate, ask-for-help accuracy.
