# Implementation plan: skill-retrieve + near-dup consolidation

**Status:** IMPLEMENTED (runtime on this branch; see `aider/z/skills/near_dup.py`)  
**Slice:** fault-plan `skill-retrieve` (F3 / P1)  
**Companion:** [fault-plan-ni-verify-skills-gate.md](./fault-plan-ni-verify-skills-gate.md) § P1 skill-retrieve  
**Scope:** all models / all Z sessions — not Claude-specific (live LFU/LRU miss was the repro)  
**Tests:** `tests/basic/test_z_skill_near_dup.py`

---

## Problem (product)

Observed on LFU/cache work with a large `~/.z/skills` library:

1. Retrieve logs **capability gaps** / never “Applying skill(s)…” even when a near-sibling LRU eviction bug-pattern exists.
2. Capture under `--yes-always` then **mints another near-identical bug_pattern** instead of updating the sibling.

Root causes are in **retrieve fields + capture dedup**, not in the model’s ability to write C.

---

## Current pipeline (repo evidence)

```text
turn / reflect
  → base_coder._maybe_pull_skills
  → pull_skills_for_checkpoint
       → retrieve_skill_candidates   # Chroma then keyword
       → route_skills                # apply/skip
  → inject “Applying skill(s)…” OR capability-gap messaging

after green/force commit
  → _maybe_suggest_skill
  → save_skill_from_task             # always _persist_skill → NEW id
```

### Retrieve today

| Step | File | Behavior |
|------|------|----------|
| Chroma query | `skills/vector.py` `query` | Cosine distance; `max_distance=0.55` in session; optional `boost_for_category` |
| Fallback | `skills/session.py` | Only if Chroma returns **empty** — not if Chroma returns weak/wrong hits |
| Keyword score | `skills/index.py` `relevance_score` | Title / description / tags / triggers / languages / kind only |
| **Missing from score** | — | `symptom_description`, `root_cause_category`, `fix_technique`, `verification_method` |
| Stem folding | — | **None** (`lru` vs `lfu` are different tokens; no `cache`/`evict` family) |
| Skip UI | `base_coder._maybe_pull_skills` | Skip reasons only if `verbose`; NI often looks like “no skills” |

### Capture today

| Step | File | Behavior |
|------|------|----------|
| Generate | `skills/generate.py` | New bug_pattern fields from model |
| Persist | `skills/cli.py` `_persist_skill` | Always `store.save` + new vector upsert |
| Dedup | — | Title collision only at **accept** time (`resolve_by_name`); capture never merges |
| Yes-always | `base_coder._maybe_suggest_skill` | Bug-pattern auto-capture **allowed** — amplifies dups when retrieve missed |

---

## Design principles

1. **Retrieve before invent** — lexical/category fallback must fire when Chroma misses *or* returns nothing useful.
2. **Update, don’t clone** — near-dup capture appends evidence to an existing skill id.
3. **Silence ≠ empty index** — NI/verbose always log top-k distances + fallback + skip reasons.
4. **Thin control plane** — no second agent; helpers only around existing retrieve/capture.
5. **Escapes** — `Z_SKILL_NEAR_DUP=0`, `Z_SKILL_LEXICAL_FALLBACK=0` restore today’s behavior.

---

## Feature A — Lexical / category fallback matcher

### New module: `aider/z/skills/near_dup.py` (shared by retrieve + capture)

```text
tokenize_folded(text) -> Set[str]
  - base tokenize (reuse index.tokenize)
  - apply STEM_FAMILIES: map tokens into canonical family ids
    e.g. {lru, lfu, mru, arc} → "cache_policy"
         {evict, eviction, reclaim} → "eviction"
         {cache, caching} → "cache"
         {leak, dangling, use-after-free, uaf} → "lifetime"
         …extensible dict, not a graph

jaccard(a, b) -> float
title_similarity(a, b) -> float   # folded token Jaccard on titles
bug_field_score(task, entry) -> float
  - weight symptom_description, root_cause_category, fix_technique,
    verification_method, title, description, tags, triggers
  - exact root_cause_category string match → strong bonus
  - BUG_CONCEPTS.symptom_keywords overlap → bonus (reuse bug_concepts)

lexical_match_skills(task, index, *, kind=None, threshold, limit)
  -> List[(SkillIndexEntry, score, reason)]
```

### Wire into `retrieve_skill_candidates`

```text
1. Chroma query (unchanged)
2. If matches empty OR (Z_SKILL_LEXICAL_FALLBACK and top score < LEXICAL_TRIGGER):
     run lexical_match_skills on _SESSION_INDEX (and/or store index entries)
3. Merge by skill id (prefer higher score); keep limit
4. Always build RetrieveTrace (see Feature B)
```

**Trigger for “weak Chroma”:** e.g. best Chroma score `< 0.45` or distance near `max_distance`, even if non-empty — so LRU skill can surface for an LFU task when embedding distance alone fails.

**Defaults**

| Env | Default | Meaning |
|-----|---------|---------|
| `Z_SKILL_LEXICAL_FALLBACK` | `1` | Enable folded lexical matcher |
| `Z_SKILL_LEXICAL_THRESHOLD` | `0.28` | Min lexical score to keep |
| `Z_SKILL_CHROMA_WEAK` | `0.45` | Below this, also run lexical even if Chroma hit |

### Acceptance (unit)

- Index entry titled `release-backing-storage-during-lru-eviction` with category/tags about cache eviction.
- Task: “implement LFU cache eviction that frees backing storage”.
- With Chroma stubbed empty (or weak), `lexical_match_skills` / `retrieve_skill_candidates` returns that skill above threshold **without live embeddings**.

---

## Feature B — Retrieve attempt logging (verbose + NI)

### New small type

```python
@dataclass
class RetrieveTrace:
    chroma_available: bool
    chroma_count: int
    chroma_top: List[tuple[id, title, score, distance]]  # top-k raw
    chroma_kept: int
    lexical_ran: bool
    lexical_top: List[tuple[id, title, score, reason]]
    merged_ids: List[str]
    skip_reasons: List[str]  # filled after route
```

### When to print

- `coder.verbose` **or** `io.yes is True` **or** `Z_SKILL_RETRIEVE_LOG=1`
- Compact lines, e.g.:

```text
Skill retrieve: chroma=12 kept=0 weak; lexical=1 hit `release-backing…` score=0.61 (family:cache_policy)
Skill skip — … (existing)
Applying skill(s): …
```

If both chroma and lexical empty → explicit:

```text
Skill retrieve: no candidates (chroma empty/miss + lexical miss) — capability gaps may follow
```

So silence no longer looks like “skills system off.”

### Wire

- `retrieve_skill_candidates` returns candidates; optional out-param or module-level last trace.
- `pull_skills_for_checkpoint` attaches route skip_reasons to trace.
- `_maybe_pull_skills` prints trace when logging enabled.

---

## Feature C — Near-dup consolidation on capture

### Detect near-dup before persist

In `save_skill_from_task` (after `generate_skill`, before `_persist_skill`):

```text
if Z_SKILL_NEAR_DUP and prefer_bug_pattern (or kind==bug_pattern):
  candidates = store.list_skills() filtered to bug_pattern (+ shared)
  best = find_near_dup(new_skill, candidates)
  if best and similarity >= threshold:
    updated = merge_into_existing(best, new_skill, grounding_pack)
    persist update (same id)
    io.tool_output(f"Updated existing skill: {best.title} ({best.id[:8]})")
    return updated, False   # created=False
```

### Similarity rubric (ordered)

1. Same `root_cause_category` (non-empty) **and** title Jaccard ≥ `0.45` **or** symptom Jaccard ≥ `0.50`
2. Else title Jaccard ≥ `0.60` with folded tokens
3. Else symptom Jaccard ≥ `0.55` with folded tokens  
Else: not a near-dup → create new

Thresholds env-tunable: `Z_SKILL_NEAR_DUP_TITLE`, `Z_SKILL_NEAR_DUP_SYMPTOM` (optional; ship constants first).

### Merge semantics (`merge_into_existing`)

Keep **existing id / path / title** (stable retrieve key). Append:

- `root_cause_explanation` / `fix_technique` / `verification_method` if new text adds substance (dedupe paragraphs)
- `source_files` / `grounded_symbols` union
- tags / triggers union
- Optional `content` section `## Additional evidence (captured …)` with truncated new body
- Bump a simple `capture_count` in frontmatter if field exists; else note in content
- Re-`upsert_skill_vector` same id
- Stay `draft` / `needs_review=True` if either side was draft (don’t auto-promote)

### Acceptance (unit)

- Store with one skill; capture with near-dup title/symptom → `list_skills()` length unchanged; id stable; message “Updated existing skill”.

---

## Feature D — Optional `z skill dedupe` (P1.5, same PR or follow-up)

```text
z skill dedupe           # dry-run: print near-dup clusters
z skill dedupe --apply   # merge clusters (keep oldest verified, else oldest id)
```

Reuse `find_near_dup` / `merge_into_existing`. Not required for the LFU false-completion claim; nice for library hygiene.

**Recommendation:** stub CLI help + dry-run in the same PR if cheap; `--apply` can be follow-up.

---

## File-level work list

| File | Change |
|------|--------|
| **new** `aider/z/skills/near_dup.py` | Folding, Jaccard, lexical_match, find_near_dup, merge_into_existing |
| `aider/z/skills/index.py` | Optionally call folded score from near_dup; keep `relevance_score` for back-compat or delegate |
| `aider/z/skills/session.py` | Weak-Chroma → lexical; RetrieveTrace; export last trace |
| `aider/z/skills/cli.py` | `save_skill_from_task` near-dup gate; optional `cmd_skill_dedupe` |
| `aider/coders/base_coder.py` | Print retrieve trace under verbose/NI |
| **new** `tests/basic/test_z_skill_near_dup.py` | Lexical LFU↔LRU; capture merge count |
| `docs/uncertainty/fault-plan-…` | Mark skill-retrieve shipped when code lands |

---

## Test plan

| Test | Asserts |
|------|---------|
| `test_stem_folding_lru_lfu` | Folded token sets intersect on cache family |
| `test_lexical_retrieves_lru_skill_for_lfu_task` | Chroma stub empty → hit LRU skill |
| `test_weak_chroma_still_runs_lexical` | Low chroma score triggers lexical merge |
| `test_retrieve_trace_fields` | Trace has chroma_kept / lexical_ran |
| `test_capture_near_dup_updates_not_creates` | Skill count stable; id unchanged |
| `test_near_dup_disabled_creates_new` | `Z_SKILL_NEAR_DUP=0` → count +1 |
| Existing `test_z_skills.py` / router / grounding | No regressions |

No live Chroma/embeddings required for the lexical + merge tests (stub `get_skill_vector_index` like session tests already do).

---

## Rollout / flags

| Env | Default | Meaning |
|-----|---------|---------|
| `Z_SKILL_LEXICAL_FALLBACK` | `1` | Folded lexical matcher after miss/weak Chroma |
| `Z_SKILL_LEXICAL_THRESHOLD` | `0.28` | Min lexical score |
| `Z_SKILL_CHROMA_WEAK` | `0.45` | Score below → also run lexical |
| `Z_SKILL_NEAR_DUP` | `1` | Merge near-dup captures |
| `Z_SKILL_RETRIEVE_LOG` | off | Force retrieve trace even if not verbose/NI |

---

## Implementation order (within this slice)

1. **`near_dup.py` core** — tokenize_folded, jaccard, lexical_match, find_near_dup (pure, easy tests)
2. **Wire retrieve** — session.py + weak chroma trigger + tests with Chroma stubbed
3. **RetrieveTrace logging** — base_coder print path
4. **Capture merge** — save_skill_from_task + persist update path
5. **Docs + optional dedupe dry-run**

Each step independently testable; prefer one PR for the whole slice unless size balloons.

---

## Non-goals

- Replacing Chroma with lexical-only retrieval.
- Auto-accepting merged drafts as verified.
- Cross-user remote dedupe on the workspace server (local store first).
- Changing capability-plan messaging semantics beyond clearer retrieve logs.

---

## Success metric

> An LFU/cache eviction task must retrieve (or category-match) an existing LRU/eviction bug-pattern via lexical fallback when embeddings miss, and a yes-always capture must update that skill instead of cloning a near-dup title into `~/.z/skills`.

Landed: lexical fallback (stem folding + bug fields), weak-chroma trigger,
`RetrieveTrace` logging under verbose/NI/`Z_SKILL_RETRIEVE_LOG`, and capture
near-dup merge via `save_skill_from_task` → `Updated existing skill`.

---

## Appendix — smoking-gun anchors

| Issue | Anchor |
|-------|--------|
| Fallback only on empty Chroma | `session.retrieve_skill_candidates` `if not matches:` |
| Keyword ignores bug fields | `index.relevance_score` fields list |
| Capture always new | `cli.save_skill_from_task` → `_persist_skill` |
| Skip silence | `base_coder._maybe_pull_skills` `verbose and skip_reasons` |
| Auto bug capture under yes-always | `base_coder._maybe_suggest_skill` `auto_bug_capture` |
