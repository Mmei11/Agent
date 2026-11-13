"""Phase C 补充：openFDA / PubMed 外部工具的解析、摘要与确定性回答。"""

from types import SimpleNamespace

import pytest

from backend.app.agent import opening
from backend.app.agent.general import (
    external_answer_is_safe,
    is_drug_label_question,
    is_research_question,
)
from backend.app.agent.opening import (
    _english_drug_name,
    _research_query,
    answer_drug_label,
    answer_research,
)
from backend.app.agent.state import ConsultationState
from backend.app.tools.contract import ToolResult
from backend.app.tools.external_summaries import (
    parse_openfda_label,
    summarize_openfda_label,
    summarize_pubmed_search,
)

OPENFDA_TEXT = """**1186 total label results** (returned: 1, skip: 0, limit: 1) | Data updated: 2026-11-12
### Ibuprofen Dye Free (IBUPROFEN)
**Manufacturer:** CVS Pharmacy
**Effective date:** 20260429
**Set ID:** 00653b7c-7099-487e-9a01-e89781c21323 (v17)
**Product type:** HUMAN OTC DRUG
**Indications and usage:**
temporarily relieves minor aches and pains due to headache.
**Warnings:**
Allergy alert: Ibuprofen may cause a severe allergic reaction. Stomach bleeding warning: This product contains an NSAID. Do not take more than 4 doses in 24 hours.
**Do not use:**
if you have ever had an allergic reaction to any other pain reliever.
**Dosage and administration:**
adults and children 12 years and over: take 1 tablet every 4 to 6 hours.
"""

PUBMED_TEXT = """## PubMed Search Results
**Query:** community acquired pneumonia antibiotic duration
**Effective Query:** community acquired pneumonia antibiotic duration
**Total Found:** 886 | **Returned:** 3 | **Offset:** 0

### Summaries

#### Diagnosis and Management of Community-acquired Pneumonia: An Official American Thoracic Society Clinical Practice Guideline.
**PMID:** 40679934
**Authors:** Jones BE, Ramirez JA
**Source:** Am J Respir Crit Care Med
**Published:** 2026-01-01
**DOI:** 10.1164/rccm.202507-1692ST
**PubMed:** https://pubmed.ncbi.nlm.nih.gov/40679934/

#### Duration of antibiotic treatment for community-acquired pneumonia.
**PMID:** 40749837
**Source:** Infect Dis Now
**Published:** 2025-09-01
**DOI:** 10.1016/j.idnow.2025.105125
"""


def _registered():
    return SimpleNamespace(get=lambda name: object())


def _state(message: str) -> ConsultationState:
    return ConsultationState(latest_user_message=message)


def test_drug_label_question_detection():
    assert is_drug_label_question("布洛芬的说明书里有什么警示？")
    assert is_drug_label_question("查一下 openFDA 上布洛芬的禁忌")
    assert not is_drug_label_question("头痛两天了")


def test_research_question_detection():
    assert is_research_question("PubMed 上关于肺炎的研究")
    assert is_research_question("社区获得性肺炎抗生素疗程有什么研究？")
    assert not is_research_question("我需要挂什么科？")


def test_parse_openfda_label_extracts_sections():
    parsed = parse_openfda_label({"text": OPENFDA_TEXT})

    assert parsed["title"] == "Ibuprofen Dye Free (IBUPROFEN)"
    assert parsed["meta"]["Manufacturer"] == "CVS Pharmacy"
    assert "Allergy alert" in parsed["sections"]["Warnings"]
    assert "take 1 tablet" in parsed["sections"]["Dosage and administration"]


def test_openfda_summary_keeps_warning_and_drops_dose_sentence():
    summary = summarize_openfda_label({"text": OPENFDA_TEXT})

    assert "Ibuprofen Dye Free" in summary
    assert "Allergy alert" in summary
    assert "4 doses" not in summary


def test_pubmed_summary_lists_titles():
    summary = summarize_pubmed_search({"text": PUBMED_TEXT})

    assert "共 886 条" in summary
    assert "Diagnosis and Management" in summary


def test_english_drug_name_maps_chinese_alias():
    assert _english_drug_name("布洛芬的说明书") == "ibuprofen"
    assert _english_drug_name("氯雷他定有什么禁忌") == "loratadine"
    assert _english_drug_name("一个不存在的药") == ""


def test_research_query_strips_chinese_framing():
    assert (
        _research_query("帮我查一下 PubMed 上关于 community acquired pneumonia 的研究")
        == "community acquired pneumonia"
    )


def test_answer_drug_label_formats_and_excludes_dosage(monkeypatch):
    monkeypatch.setattr(opening, "DEFAULT_CATALOG", _registered())
    monkeypatch.setattr(
        opening,
        "call_tool",
        lambda name, params: ToolResult(
            ok=True, name=name, data={"text": OPENFDA_TEXT}, latency_ms=5
        ),
    )

    result = answer_drug_label(_state("布洛芬的说明书里有什么警示？"))

    text = result["rendered_text"]
    assert "FDA 说明书" in text
    assert "Allergy alert" in text
    assert "take 1 tablet" not in text
    assert "来源：openFDA" in text
    assert result["tool_results"]["openfda_get_drug_label"]["text"] == OPENFDA_TEXT
    assert result["trace"][1]["called"] == "openfda_get_drug_label"


def test_answer_research_formats_citations(monkeypatch):
    monkeypatch.setattr(opening, "DEFAULT_CATALOG", _registered())
    monkeypatch.setattr(
        opening,
        "call_tool",
        lambda name, params: ToolResult(
            ok=True, name=name, data={"text": PUBMED_TEXT}, latency_ms=5
        ),
    )

    result = answer_research(_state("PubMed 上关于肺炎的研究"))

    text = result["rendered_text"]
    assert "PubMed 检索" in text
    assert "PMID: 40679934" in text
    assert "不构成诊断或治疗建议" in text
    assert result["trace"][0]["pubmed"] is True


def test_external_paths_disabled_return_none(monkeypatch):
    monkeypatch.setattr(opening, "DEFAULT_CATALOG", SimpleNamespace(get=lambda name: None))

    assert answer_drug_label(_state("布洛芬的说明书")) is None
    assert answer_research(_state("PubMed 上关于肺炎的研究")) is None


@pytest.mark.parametrize(
    "text",
    [
        "警示：每次 400 mg，每日两次。来源：openFDA",
        "建议服用布洛芬。来源：openFDA",
        "没有来源的普通文本",
    ],
)
def test_external_answer_gate_rejects_unsafe_text(text):
    assert not external_answer_is_safe(text)
