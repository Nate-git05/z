"""Z skills — reusable, auto-discovered instruction files (ChromaDB retrieval)."""

from .generate import generate_skill
from .schema import Skill, SkillIndexEntry
from .router import route_skills, collect_repo_signals
from .session import (
    format_skill_metadata,
    format_skills_for_context,
    get_session_skill_index,
    load_skills_for_session,
    print_skills_list,
    pull_skills_for_checkpoint,
    select_relevant_skills,
)
from .store import LocalSkillStore, skills_dir
from .vector import SkillVectorIndex, get_skill_vector_index

__all__ = [
    "Skill",
    "SkillIndexEntry",
    "LocalSkillStore",
    "SkillVectorIndex",
    "skills_dir",
    "generate_skill",
    "load_skills_for_session",
    "get_session_skill_index",
    "select_relevant_skills",
    "pull_skills_for_checkpoint",
    "route_skills",
    "collect_repo_signals",
    "format_skills_for_context",
    "format_skill_metadata",
    "print_skills_list",
    "get_skill_vector_index",
]
