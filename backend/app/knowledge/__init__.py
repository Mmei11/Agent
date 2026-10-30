"""Phase 4：受控知识库。条目为项目归纳，出处待逐条核实。"""

from backend.app.knowledge.schema import (
    KnowledgeEntry,
    ScopePayload,
    Source,
    load_entries,
    load_sources,
)

__all__ = [
    "KnowledgeEntry",
    "ScopePayload",
    "Source",
    "load_entries",
    "load_sources",
]