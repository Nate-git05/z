"""Curated bug-pattern taxonomy + per-language manifestation notes.

Flat lookup table (not a graph). Same curated-list philosophy as
``established_solutions.py``: append a row to add a concept — don't invent
infrastructure for multi-hop reasoning until real usage demands it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class BugConcept:
    """One root-cause category with grounding evidence + per-language notes."""

    category_id: str
    title: str
    description: str
    # Match added (+) diff lines that ground a claim of this category
    evidence_regex: re.Pattern
    # Optional: keywords that boost retrieval when present in the new bug text
    symptom_keywords: Tuple[str, ...]
    # language → manifestation / mitigation note
    language_notes: Dict[str, str]


BUG_CONCEPTS: Tuple[BugConcept, ...] = (
    BugConcept(
        category_id="missing_synchronization_for_shared_state",
        title="Missing synchronization for shared state",
        description=(
            "Cross-thread shared memory published/consumed without a real "
            "visibility/ordering guarantee (bare volatile, plain loads/stores)."
        ),
        evidence_regex=re.compile(
            r"(?x)"
            r"\b(?:std\s*::\s*)?atomic\b"
            r"|memory_order_(?:relaxed|acquire|release|acq_rel|seq_cst)"
            r"|\.store\s*\(|\.load\s*\("
            r"|\b(?:mutex|lock_guard|unique_lock|shared_mutex|std::lock)\b"
            r"|\bsync\.(?:Mutex|RWMutex|WaitGroup)\b"
            r"|\batomic\.(?:Add|Load|Store|CompareAndSwap)\w*\b"
            r"|Ordering::(?:Relaxed|Acquire|Release|AcqRel|SeqCst)",
            re.IGNORECASE,
        ),
        symptom_keywords=(
            "segfault",
            "race",
            "data race",
            "intermittent",
            "thread",
            "tsan",
            "threadsanitizer",
            "heisenbug",
            "crash under load",
        ),
        language_notes={
            "cpp": (
                "raw volatile/plain read-write has no cross-thread visibility "
                "guarantee; needs std::atomic + memory_order_release/acquire"
            ),
            "c": (
                "plain shared fields need C11 atomics (_Atomic / stdatomic.h) "
                "or a mutex; volatile is not a concurrency primitive"
            ),
            "rust": (
                "safe Rust's borrow checker prevents this for ordinary references; "
                "reappears via unsafe blocks or std::sync::atomic with wrong Ordering"
            ),
            "go": (
                "goroutines sharing memory without a mutex/channel hit this the "
                "same way; -race is first-class, cheap tooling — check whether it "
                "was actually run before treating a clean pass as verified"
            ),
            "java": (
                "shared fields need volatile, synchronized, or java.util.concurrent "
                "atomics; unsynchronized reads can observe stale values"
            ),
            "python": (
                "the GIL hides some races but not all (esp. C extensions / "
                "multiprocessing shared memory); prefer Queue/Lock over bare shared lists"
            ),
        },
    ),
    BugConcept(
        category_id="use_after_free",
        title="Use after free / dangling reference",
        description=(
            "Memory or object used after it was freed/dropped/moved — classic "
            "ASan use-after-free / dangling pointer."
        ),
        evidence_regex=re.compile(
            r"(?x)"
            r"\b(?:free|delete|delete\s*\[\]|Drop::drop|mem::drop|Box::from_raw)\b"
            r"|\b(?:std::move|std::unique_ptr|shared_ptr|weak_ptr)\b"
            r"|\.reset\s*\(|take\s*\("
            # Handle + generation-counter indirection (slotmaps / registries / ECS)
            r"|\.resolve\s*\("
            r"|\w*Handle\b"
            r"|generation\s*(?:count|id|counter)",
            re.IGNORECASE,
        ),
        symptom_keywords=(
            "use-after-free",
            "use after free",
            "segfault",
            "asan",
            "addresssanitizer",
            "dangling",
            "heap-use-after-free",
        ),
        language_notes={
            "cpp": (
                "raw new/delete and iterator/reference into reallocated containers "
                "are common; AddressSanitizer is the first verification tool"
            ),
            "c": "free'd pointers must be nulled or ownership transferred; ASan catches most UAFs",
            "rust": (
                "safe Rust prevents this; look for unsafe, raw pointers, or "
                "self-referential structs that outlive their borrow"
            ),
            "go": "GC makes classic UAF rare; still appears with cgo / unsafe.Pointer",
        },
    ),
    BugConcept(
        category_id="iterator_reference_invalidation",
        title="Iterator / reference invalidation",
        description=(
            "Holding an index, iterator, or reference across a reallocation or "
            "mutation (e.g. vector::emplace_back while another thread reads by index)."
        ),
        evidence_regex=re.compile(
            r"(?x)"
            r"\b(?:emplace_back|push_back|resize|reserve|insert|erase|clear)\b"
            r"|\.reserve\s*\(|\.resize\s*\("
            r"|\b(?:iterator|const_iterator)\b",
            re.IGNORECASE,
        ),
        symptom_keywords=(
            "segfault",
            "invalidation",
            "reallocation",
            "vector",
            "iterator",
            "index",
            "asan",
            "heap-buffer-overflow",
        ),
        language_notes={
            "cpp": (
                "std::vector reallocation invalidates all references/iterators; "
                "across threads this races with unguarded index reads — fix with "
                "stable storage, indices under a lock, or pre-reserve"
            ),
            "rust": "borrow checker blocks most cases; watch RefCell/UnsafeCell and index caches",
            "go": "append may reallocate the backing array; aliases of old slice headers go stale",
        },
    ),
    BugConcept(
        category_id="toctou_race",
        title="TOCTOU / check-then-act race",
        description=(
            "A check and a subsequent use are not atomic — another thread/process "
            "changes the state in between."
        ),
        evidence_regex=re.compile(
            r"(?x)"
            r"\b(?:compare_exchange|compareAndSwap|CompareAndSwap|"
            r"atomic\.(?:CompareAndSwap|Value)|Mutex|Lock|flock|O_EXCL|os\.O_EXCL)\b",
            re.IGNORECASE,
        ),
        symptom_keywords=(
            "toctou",
            "race",
            "intermittent",
            "exists",
            "check then",
            "file race",
        ),
        language_notes={
            "cpp": "collapse check+act into one atomic/CAS or hold a mutex across both",
            "go": "use atomic ops or a mutex; file TOCTOU needs O_EXCL / rename patterns",
            "python": "os.path.exists then open is classic TOCTOU — prefer exclusive create",
            "rust": "prefer atomic CAS or lock across the critical section",
        },
    ),
    BugConcept(
        category_id="buffer_overflow_or_oob",
        title="Buffer overflow / out-of-bounds access",
        description="Write or read past the end of a buffer or array.",
        evidence_regex=re.compile(
            r"(?x)"
            r"\b(?:memcpy|memmove|strcpy|strncpy|sprintf|gets|snprintf)\b"
            r"|\.at\s*\(|bounds.?check|size\(\)\s*[-+]|len\s*-",
            re.IGNORECASE,
        ),
        symptom_keywords=(
            "buffer overflow",
            "heap-buffer-overflow",
            "stack-buffer-overflow",
            "oob",
            "out of bounds",
            "asan",
            "segfault",
        ),
        language_notes={
            "cpp": "prefer span/size-checked APIs; verify with AddressSanitizer",
            "c": "never strcpy/gets into fixed buffers; ASan + bounded helpers",
            "rust": "safe indexing panics; unsafe get_unchecked / raw pointers reintroduce OOB",
            "go": "slice bounds panic; watch unsafe and cgo buffers",
        },
    ),
    BugConcept(
        category_id="resource_leak",
        title="Resource / memory leak",
        description="Allocation without a matching free/close on all paths.",
        evidence_regex=re.compile(
            r"(?x)"
            r"(?:"
            r"\b(?:free|delete|Close|close|Dispose|defer\s+\w+\.Close|"
            r"unique_ptr|shared_ptr|RAII|contextlib|with\s+|"
            r"erase|pop_back|pop_front|remove)\b"
            # .clear() can't sit inside \b...\b — ')' is non-word so the
            # trailing boundary never matches.
            r"|\.clear\s*\("
            r")",
            re.IGNORECASE,
        ),
        symptom_keywords=(
            "leak",
            "oom",
            "out of memory",
            "lsan",
            "leaksanitizer",
            "valgrind",
            "definitely lost",
        ),
        language_notes={
            "cpp": "prefer RAII (unique_ptr); verify with LeakSanitizer/valgrind",
            "c": "pair every malloc with free on all paths; LSan/valgrind",
            "go": "defer Close(); watch goroutines that never exit",
            "rust": "Drop usually handles it; look for ManuallyDrop / mem::forget / Box::leak",
            "python": "use with/contextlib; C-extension allocs need explicit free",
        },
    ),
)


def taxonomy_category_ids() -> List[str]:
    return [c.category_id for c in BUG_CONCEPTS]


def concept_by_id(category_id: str) -> Optional[BugConcept]:
    cid = (category_id or "").strip().lower()
    for c in BUG_CONCEPTS:
        if c.category_id == cid:
            return c
    return None


def language_note(category_id: str, language: str) -> Optional[str]:
    concept = concept_by_id(category_id)
    if not concept:
        return None
    lang = (language or "").strip().lower()
    # normalize aliases
    aliases = {
        "c++": "cpp",
        "cplusplus": "cpp",
        "cxx": "cpp",
        "golang": "go",
        "py": "python",
        "js": "javascript",
        "ts": "typescript",
    }
    lang = aliases.get(lang, lang)
    if lang in concept.language_notes:
        return concept.language_notes[lang]
    return None


def _added_blob(diff: str) -> str:
    lines = []
    for line in (diff or "").splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            lines.append(line[1:])
    return "\n".join(lines)


def category_grounded_in_diff(category_id: str, diff: str) -> Tuple[bool, str]:
    """
    Whether a claimed root_cause_category is evidenced by the real diff.

    Same spirit as established_solutions invention/standard checks.
    """
    concept = concept_by_id(category_id)
    if not concept:
        return False, f"unknown root_cause_category: {category_id}"
    blob = _added_blob(diff)
    if not blob.strip():
        return False, "no added diff lines to ground the diagnosis"
    if concept.evidence_regex.search(blob):
        return True, "evidence matched in added diff"
    return (
        False,
        f"diff lacks evidence for {category_id} "
        f"(expected patterns like {concept.evidence_regex.pattern[:80]}…)",
    )


def match_symptom_keywords(text: str) -> List[str]:
    """Return category_ids whose symptom keywords appear in text."""
    blob = (text or "").lower()
    hits: List[str] = []
    for c in BUG_CONCEPTS:
        if any(k in blob for k in c.symptom_keywords):
            hits.append(c.category_id)
    return hits


def boost_for_category(score: float, category_id: str, bug_text: str) -> float:
    """Boost retrieval score when the new bug text matches the category keywords."""
    concept = concept_by_id(category_id)
    if not concept:
        return score
    blob = (bug_text or "").lower()
    hits = sum(1 for k in concept.symptom_keywords if k in blob)
    if hits <= 0:
        return score
    # Bounded boost — keyword match is a strong prior but not proof
    return min(1.0, score + 0.08 * min(hits, 3))
