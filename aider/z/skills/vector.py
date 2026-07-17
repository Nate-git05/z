"""ChromaDB-backed skill vector index (metadata + path; body stays on disk)."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Sequence

from .schema import Skill, SkillIndexEntry, _as_str_list
from .store import chroma_dir


COLLECTION_NAME = "z_skills"


def _meta_to_chroma(skill: Skill) -> dict:
    # Chroma metadata values must be scalar
    from .schema import SKILL_KIND_BUG_PATTERN

    return {
        "id": skill.id,
        "title": skill.title or "",
        "description": skill.description or "",
        "path": skill.path or "",
        "filename": skill.filename or "",
        "scope": skill.scope or "personal",
        "source": skill.source or "generate",
        "remote_id": skill.remote_id or "",
        "tags": ",".join(skill.tags or []),
        "project_types": ",".join(skill.project_types or []),
        "triggers": ",".join(skill.triggers or []),
        "languages": ",".join(skill.languages or []),
        "kind": skill.kind or "playbook",
        "artifacts": ",".join(skill.artifacts or []),
        "apply_once": "1" if skill.apply_once else "0",
        "symptom_description": (skill.symptom_description or "")[:500],
        "root_cause_category": skill.root_cause_category or "",
        "fix_technique": (skill.fix_technique or "")[:300],
        "verification_method": (skill.verification_method or "")[:200],
        "language": skill.language
        or ((skill.languages or [""])[0] if skill.languages else ""),
        "pool": (
            "bug_pattern"
            if (skill.kind or "") == SKILL_KIND_BUG_PATTERN
            else "feature"
        ),
    }


def _entry_from_chroma(meta: dict, *, distance: float | None = None) -> SkillIndexEntry:
    from .schema import VALID_SKILL_KINDS

    kind = (meta.get("kind") or "playbook").strip().lower()
    if kind not in VALID_SKILL_KINDS:
        kind = "playbook"
    apply_once = str(meta.get("apply_once") or "").strip() in ("1", "true", "yes")
    if meta.get("apply_once") in (None, ""):
        apply_once = kind == "scaffold"
    language = (meta.get("language") or "").strip().lower()
    languages = _as_str_list(meta.get("languages"))
    if language and language not in languages:
        languages = [language] + list(languages)
    return SkillIndexEntry(
        id=str(meta.get("id") or ""),
        title=meta.get("title") or "",
        description=meta.get("description") or "",
        scope=meta.get("scope") or "personal",
        source=meta.get("source") or "local",
        remote_id=meta.get("remote_id") or None,
        filename=meta.get("filename") or None,
        path=meta.get("path") or None,
        tags=_as_str_list(meta.get("tags")),
        project_types=_as_str_list(meta.get("project_types")),
        triggers=_as_str_list(meta.get("triggers")),
        languages=languages,
        kind=kind,
        artifacts=_as_str_list(meta.get("artifacts")),
        apply_once=apply_once,
        symptom_description=(meta.get("symptom_description") or "").strip(),
        root_cause_category=(meta.get("root_cause_category") or "").strip(),
        fix_technique=(meta.get("fix_technique") or "").strip(),
        verification_method=(meta.get("verification_method") or "").strip(),
        language=language or (languages[0] if languages else ""),
    )


class SkillVectorIndex:
    """Persistent Chroma collection under ~/.z/chroma/skills."""

    def __init__(self, persist_dir: Optional[Path] = None):
        self.persist_dir = Path(persist_dir) if persist_dir else chroma_dir()
        self.persist_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._client = None
        self._collection = None

    @property
    def available(self) -> bool:
        try:
            import chromadb  # noqa: F401

            return True
        except ImportError:
            return False

    def _ensure(self):
        if self._collection is not None:
            return self._collection
        if not self.available:
            raise RuntimeError(
                "chromadb is not installed. Install with: pip install chromadb"
            )
        import chromadb

        self._client = chromadb.PersistentClient(path=str(self.persist_dir))
        self._collection = self._client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
        return self._collection

    def upsert(self, skill: Skill) -> None:
        if not skill.id:
            return
        col = self._ensure()
        doc = skill.embed_text() or skill.title or skill.id
        col.upsert(
            ids=[skill.id],
            documents=[doc],
            metadatas=[_meta_to_chroma(skill)],
        )

    def delete(self, skill_id: str) -> None:
        if not skill_id:
            return
        col = self._ensure()
        try:
            col.delete(ids=[skill_id])
        except Exception:
            pass

    def query(
        self,
        task: str,
        *,
        k: int = 3,
        max_distance: float = 0.75,
        kind: Optional[str] = None,
        pool: Optional[str] = None,
        boost_bug_text: Optional[str] = None,
    ) -> List[tuple[SkillIndexEntry, float]]:
        """
        Return (entry, score) where score is 1 - cosine distance (higher is better).

        ``kind`` / ``pool`` filter the logical pool (e.g. bug_pattern vs feature)
        inside the same Chroma collection — no second infrastructure.
        """
        task = (task or "").strip()
        if not task:
            return []
        col = self._ensure()
        if col.count() == 0:
            return []
        n = min(max(k * 3, k), max(col.count(), 1))
        where = None
        if kind:
            where = {"kind": kind}
        elif pool:
            where = {"pool": pool}
        try:
            if where is not None:
                result = col.query(query_texts=[task], n_results=n, where=where)
            else:
                result = col.query(query_texts=[task], n_results=n)
        except Exception:
            # Older docs may lack pool/kind metadata — fall back unfiltered
            result = col.query(query_texts=[task], n_results=n)
        ids = (result.get("ids") or [[]])[0]
        metas = (result.get("metadatas") or [[]])[0]
        dists = (result.get("distances") or [[]])[0]
        out: List[tuple[SkillIndexEntry, float]] = []
        for i, sid in enumerate(ids):
            meta = metas[i] if i < len(metas) else {"id": sid}
            if kind and (meta.get("kind") or "") != kind:
                continue
            if pool and (meta.get("pool") or "") != pool:
                # tolerate legacy rows without pool when kind matches
                if kind is None and (meta.get("kind") or "") != pool:
                    continue
            dist = float(dists[i]) if i < len(dists) else 1.0
            if dist > max_distance:
                continue
            score = max(0.0, 1.0 - dist)
            entry = _entry_from_chroma(meta, distance=dist)
            if boost_bug_text and entry.root_cause_category:
                try:
                    from .bug_concepts import boost_for_category

                    score = boost_for_category(
                        score, entry.root_cause_category, boost_bug_text
                    )
                except Exception:
                    pass
            out.append((entry, score))
        out.sort(key=lambda x: x[1], reverse=True)
        return out[:k]

    def reindex(self, skills: Sequence[Skill]) -> int:
        """Replace collection contents with the provided skills."""
        col = self._ensure()
        # Drop and recreate for a clean rebuild
        if self._client is not None:
            try:
                self._client.delete_collection(COLLECTION_NAME)
            except Exception:
                pass
            self._collection = self._client.get_or_create_collection(
                name=COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"},
            )
            col = self._collection
        count = 0
        for skill in skills:
            if not skill.id:
                continue
            self.upsert(skill)
            count += 1
        return count

    def count(self) -> int:
        try:
            return int(self._ensure().count())
        except Exception:
            return 0


_INDEX: Optional[SkillVectorIndex] = None


def get_skill_vector_index(persist_dir: Optional[Path] = None) -> SkillVectorIndex:
    global _INDEX
    if persist_dir is not None:
        return SkillVectorIndex(persist_dir=persist_dir)
    if _INDEX is None:
        _INDEX = SkillVectorIndex()
    return _INDEX


def upsert_skill_vector(skill: Skill, *, persist_dir: Optional[Path] = None) -> bool:
    try:
        get_skill_vector_index(persist_dir).upsert(skill)
        return True
    except Exception:
        return False
