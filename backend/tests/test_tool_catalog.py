"""Phase C：工具目录与风险分级。"""

import pytest
from pydantic import BaseModel

from backend.app.tools.catalog import DEFAULT_CATALOG, ToolCatalog
from backend.app.tools.contract import ToolSpec


class _Params(BaseModel):
    query: str = ""


def _spec(
    name: str, risk_class: str = "read_only", scope: str = "internal"
) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=f"{name} 测试工具",
        params_model=_Params,
        handler=lambda params: {"ok": True},
        risk_class=risk_class,
        scope=scope,
    )


def test_register_and_get():
    catalog = ToolCatalog([_spec("a")])

    assert catalog.get("a").name == "a"
    assert catalog.get("missing") is None
    assert catalog.names() == ["a"]


def test_duplicate_registration_is_rejected():
    catalog = ToolCatalog([_spec("a")])

    with pytest.raises(ValueError):
        catalog.register(_spec("a"))


def test_only_read_only_tools_are_auto_callable():
    catalog = ToolCatalog(
        [
            _spec("read"),
            _spec("write", risk_class="write"),
            _spec("sensitive", risk_class="sensitive"),
        ]
    )

    assert [spec.name for spec in catalog.auto_callable()] == ["read"]
    assert [entry["name"] for entry in catalog.prompt_entries()] == ["read"]


def test_prompt_entries_carry_risk_and_schema():
    entry = ToolCatalog([_spec("read")]).prompt_entries()[0]

    assert entry["risk_class"] == "read_only"
    assert entry["scope"] == "internal"
    assert entry["input_schema"]["type"] == "object"


def test_unknown_tool_returns_structured_failure():
    result = ToolCatalog().call("missing", {})

    assert result.ok is False
    assert result.error_kind == "invalid_params"


def test_call_executes_registered_tool():
    result = ToolCatalog([_spec("read")]).call("read", {})

    assert result.ok is True
    assert result.data == {"ok": True}


def test_default_catalog_contains_three_read_only_knowledge_tools():
    assert DEFAULT_CATALOG.names() == [
        "department_route",
        "guideline_retrieve",
        "red_flag_lookup",
    ]
    assert all(spec.auto_callable for spec in DEFAULT_CATALOG.auto_callable())
