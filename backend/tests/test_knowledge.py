"""Phase 4 第一课：知识库的结构与引用完整性。"""

import pytest

from backend.app.knowledge.schema import (
    KnowledgeEntry,
    ScopePayload,
    load_entries,
    load_sources,
    validate_store,
)


def _entry(**overrides) -> KnowledgeEntry:
    base = {
        "id": "route-test",
        "category": "department_route",
        "title": "测试条目",
        "content": "测试内容",
        "source_id": "src-project-derived",
        "scope_payload": ScopePayload(symptom_cluster=["头痛"]),
        "scope_text": "测试范围",
        "version": "v1",
    }
    base.update(overrides)
    return KnowledgeEntry(**base)


def test_store_loads_without_problems():
    assert validate_store() == []


def test_first_batch_is_department_routing():
    entries = load_entries()
    assert len(entries) >= 12
    assert {entry.category for entry in entries} == {"department_route"}


def test_every_entry_carries_scope_and_version():
    for entry in load_entries():
        assert entry.scope_payload.symptom_cluster, entry.id
        assert entry.scope_text, entry.id
        assert entry.version == "v1"


def test_unverified_sources_are_discoverable():
    """D1 的出处尚未核实，必须能从数据里查出来，而不是藏在注释里。"""
    unverified = [source.id for source in load_sources().values() if not source.verified]
    assert unverified == ["src-project-derived"]


def test_entry_referencing_missing_source_is_rejected():
    problems = validate_store(entries=[_entry(id="route-x", source_id="src-missing")])
    assert any("不存在的出处" in problem for problem in problems)


def test_duplicate_id_is_rejected():
    problems = validate_store(entries=[_entry(), _entry()])
    assert any("id 重复" in problem for problem in problems)


def test_empty_symptom_cluster_is_rejected():
    problems = validate_store(
        entries=[_entry(scope_payload=ScopePayload(symptom_cluster=[]))]
    )
    assert any("symptom_cluster 为空" in problem for problem in problems)


def test_reversed_age_range_is_rejected():
    problems = validate_store(
        entries=[
            _entry(
                scope_payload=ScopePayload(
                    symptom_cluster=["头痛"], age_min=60, age_max=18
                )
            )
        ]
    )
    assert any("年龄区间颠倒" in problem for problem in problems)


def test_unknown_field_is_forbidden():
    with pytest.raises(ValueError):
        KnowledgeEntry(
            id="route-bad",
            category="department_route",
            title="标题",
            content="内容",
            source_id="src-project-derived",
            scope_payload=ScopePayload(symptom_cluster=["头痛"]),
            scope_text="范围",
            version="v1",
            unexpected="x",
        )