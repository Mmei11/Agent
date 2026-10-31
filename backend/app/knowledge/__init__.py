"""Phase 4：受控知识库。条目为项目归纳，出处待逐条核实。"""

from backend.app.knowledge.indexer import build_index
from backend.app.knowledge.retrieval import Hit, retrieve
from backend.app.knowledge.schema import (
    KnowledgeEntry,
    ScopePayload,
    Source,
    load_entries,
    load_sources,
    validate_store,
)

__all__ = [
    "Hit",
    "KnowledgeEntry",
    "ScopePayload",
    "Source",
    "build_index",
    "load_entries",
    "load_sources",
    "retrieve",
    "validate_store",
]