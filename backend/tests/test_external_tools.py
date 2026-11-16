"""Phase C 补充：openFDA / PubMed 外部工具的解析、摘要与确定性回答。"""

from types import SimpleNamespace

import pytest

from backend.app.agent import opening
from backend.app.agent.general import (
    external_answer_is_safe,
    is_adverse_event_question,
    is_approval_question,
    is_clinical_trial_question,
    is_drug_label_question,
    is_health_topic_question,
    is_recall_question,
    is_research_question,
    is_shortage_question,
    translated_external_is_safe,
)
from backend.app.agent.opening import (
    _english_drug_name,
    _fetch_medlineplus_text,
    _health_topic_query,
    _research_query,
    _trial_query,
    answer_adverse_events,
    answer_approval,
    answer_clinical_trials,
    answer_drug_label,
    answer_health_topic,
    answer_recall,
    answer_research,
    answer_shortage,
)
from backend.app.agent.state import ConsultationState
from backend.app.llm.client import LLMResult
from backend.app.tools.contract import ToolResult
from backend.app.tools.external_summaries import (
    parse_openfda_label,
    summarize_clinicaltrials_search,
    summarize_medlineplus_search,
    summarize_openfda_adverse_events,
    summarize_openfda_approvals,
    summarize_openfda_label,
    summarize_openfda_recalls,
    summarize_openfda_shortages,
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

RECALL_TEXT = """**31 total results** (returned: 2, skip: 0, limit: 2) | Data updated: 2026-11-04

**Recall #D-0583-2018** — Class II
Firm: Spirit Pharmaceuticals, LLC
Product: Ibuprofen Tablets USP, 200 mg, 100-count bottles
Reason: CGMP deviations: Ibuprofen is being recalled in response to previous recall
Status: Terminated
Recall initiation date: 20180110
**Openfda:** generic_name=IBUPROFEN; product_ndc=68210-0800

---

**Recall #D-1055-2017** — Class II
Product: Ibuprofen Tablets, USP 600 mg, 500-count bottle
Reason: Failed dissolution specification
Status: Completed
Recall initiation date: 20170120
"""

TRIAL_TEXT = """Found 3 studies (443 total matching)
- **NCT02205112**: A Phase III Study to Evaluate the Efficacy and Safety of Intravenous Infusion of Nemonoxacin in Treating CAP [COMPLETED]
  PHASE3 | N=598 | TaiGen Biotechnology Co., Ltd. | Community-acquired Pneumonia | no results | start 2014-06
  Site: Baotou Central Hospital, Baotou, China (1 of 63 sites)
- **NCT06782607**: Clinical Impact of a Multiplex Rapid Diagnostic Pneumonia Panel Testing [NOT_YET_RECRUITING]
  NA | N=80 | Mahidol University | Infectious Disease of Lung | no results | start 2025-02-01
  Site: Siriraj Hospital, Bangkok, Thailand
- **NCT06170372**: High-dose Inhalations of Nitric Oxide in the Treatment of Pneumonia [ACTIVE_NOT_RECRUITING]
  NA | N=200 | Tomsk National Research Medical Center | Nosocomial Pneumonia | no results | start 2024-01-15
  Site: Cardiology Research Institute, Tomsk, Russia
"""

SHORTAGE_TEXT = """**35 total results** (returned: 1, skip: 0, limit: 1) | Data updated: 2026-11-13

### Carboplatin Injection
**Status:** Current
**Availability:** Limited Availability
**Therapeutic category:** Oncology
**Dosage form:** Injection
**Manufacturer:** Gland Pharma Limited
**Timeline:** first posted 04/28/2023 | updated 10/21/2026
"""

APPROVAL_TEXT = """**208 total results** (returned: 1, skip: 0, limit: 1) | Data updated: 2026-11-12

### IBUPROFEN
**Application:** ANDA071265 | **Sponsor:** CONTRACT PHARMACAL
**Brand:** IBUPROFEN | **Generic:** IBUPROFEN
**Products:**
- IBUPROFEN | (IBUPROFEN 200MG) | TABLET | ORAL | Discontinued
"""

AE_TEXT = """**221642 total results** (returned: 1, skip: 0, limit: 1) | Data updated: 2026-10-28

### safetyreportid: 10003301
**Patient:** reaction=[{"reactionmeddrapt":"Dyspepsia"},{"reactionmeddrapt":"Renal impairment"}]; drug=[{"medicinalproduct":"IBUPROFEN"}]
**Serious:** 1
**Receiptdate:** 20140228
"""

MEDLINE_DATA = {
    "total": 97,
    "returned": 1,
    "results": [
        {
            "title": '<span class="qt0"><span class="qt1">High Blood Pressure</span></span>',
            "snippet": "What is blood pressure? Blood pressure is the force of your blood.",
            "url": "https://medlineplus.gov/highbloodpressure.html",
        }
    ],
}

TRIAL_RECORD_TEXT = """# Study NCT02205112: A Phase III Study to Evaluate the Efficacy and Safety of Intravenous Infusion of Nemonoxacin in Treating CAP
**Status:** COMPLETED | INTERVENTIONAL | PHASE3 | N=598 (ACTUAL)
**Sponsor:** TaiGen Biotechnology Co., Ltd. (INDUSTRY)
**Conditions:** Community-acquired Pneumonia
**Dates:** Start: 2014-06 | Primary Completion: 2017-05-04 (ACTUAL) | Completion: 2017-05-15 (ACTUAL)
"""

PUBMED_ARTICLE_TEXT = """## PubMed Articles
**Articles Returned:** 1

### Diagnosis and Management of Community-acquired Pneumonia: An Official American Thoracic Society Clinical Practice Guideline.
**Journal:** American journal of respiratory and critical care medicine, 2026 Jan 1
**PMID:** 40679934
**DOI:** 10.1164/rccm.202507-1692ST

#### Abstract
BACKGROUND: Understanding of the diagnosis and treatment of adults with community-acquired pneumonia has evolved.
"""


def _registered():
    return SimpleNamespace(get=lambda name: object())


def _state(message: str) -> ConsultationState:
    return ConsultationState(latest_user_message=message)


def test_drug_label_question_detection():
    assert is_drug_label_question("布洛芬的说明书里有什么警示？")
    assert is_drug_label_question("查一下 openFDA 上布洛芬的禁忌")
    assert not is_drug_label_question("头痛两天了")
    # 批准与不良反应有各自更具体的路径，不能被说明书截走
    assert not is_drug_label_question("布洛芬 FDA 批准了吗？")
    assert not is_drug_label_question("布洛芬有什么不良反应？")


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


def test_answer_research_failure_is_not_reported_as_empty(monkeypatch):
    monkeypatch.setattr(opening, "DEFAULT_CATALOG", _registered())
    monkeypatch.setattr(
        opening,
        "call_tool",
        lambda name, params: ToolResult(
            ok=False, name=name, error="超时", error_kind="timeout"
        ),
    )

    result = answer_research(_state("PubMed 上关于肺炎的研究"))

    text = result["rendered_text"]
    assert "不可用" in text
    assert "暂时没有检索到" not in text


def test_knowledge_stub_does_not_replace_external_calls(monkeypatch):
    """KNOWLEDGE_BACKEND=stub 只能替身知识工具，不能吞掉外部工具调用。"""
    from backend.app.tools import registry

    monkeypatch.setenv("KNOWLEDGE_BACKEND", "stub")

    assert opening.call_tool is registry.call_tool


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


def test_recall_and_trial_question_detection():
    assert is_recall_question("布洛芬有没有召回？")
    assert is_clinical_trial_question("有没有相关临床试验？")
    assert not is_recall_question("头痛两天了")
    assert not is_clinical_trial_question("我需要挂什么科？")


def test_recall_summary_keeps_ids():
    summary = summarize_openfda_recalls({"text": RECALL_TEXT})

    assert "共 31 条" in summary
    assert "D-0583-2018" in summary


def test_trial_summary_keeps_nct_ids():
    summary = summarize_clinicaltrials_search({"text": TRIAL_TEXT})

    assert "共 443 条" in summary
    assert "NCT02205112" in summary


def test_trial_query_strips_chinese_framing():
    assert (
        _trial_query("有没有关于 community acquired pneumonia 的临床试验？")
        == "community acquired pneumonia"
    )


def test_answer_recall_formats_and_strips_dosage(monkeypatch):
    monkeypatch.setattr(opening, "DEFAULT_CATALOG", _registered())
    monkeypatch.setattr(
        opening,
        "call_tool",
        lambda name, params: ToolResult(
            ok=True, name=name, data={"text": RECALL_TEXT}, latency_ms=5
        ),
    )

    result = answer_recall(_state("布洛芬有没有召回？"))

    text = result["rendered_text"]
    assert "D-0583-2018" in text
    assert "Class II" in text
    assert "CGMP deviations" in text
    assert "2018-01-10" in text
    assert "200 mg" not in text
    assert "来源：openFDA" in text
    assert result["trace"][1]["called"] == "openfda_search_recalls"


def test_answer_clinical_trials_formats(monkeypatch):
    monkeypatch.setattr(opening, "DEFAULT_CATALOG", _registered())
    monkeypatch.setattr(
        opening,
        "call_tool",
        lambda name, params: ToolResult(
            ok=True, name=name, data={"text": TRIAL_TEXT}, latency_ms=5
        ),
    )

    result = answer_clinical_trials(_state("有没有关于肺炎的临床试验？"))

    text = result["rendered_text"]
    assert "NCT02205112" in text
    assert "COMPLETED" in text
    assert "共 443 条" in text
    assert "来源：ClinicalTrials.gov" in text
    assert result["trace"][0]["clinicaltrials"] is True


def test_recall_and_trial_paths_disabled_return_none(monkeypatch):
    monkeypatch.setattr(opening, "DEFAULT_CATALOG", SimpleNamespace(get=lambda name: None))

    assert answer_recall(_state("布洛芬有没有召回？")) is None
    assert answer_clinical_trials(_state("有没有相关临床试验？")) is None


def test_gate_accepts_clinicaltrials_source():
    assert external_answer_is_safe(
        "来源：ClinicalTrials.gov。试验信息不代表适合你。"
    )


def test_new_external_question_detection():
    assert is_shortage_question("布洛芬短缺吗？")
    assert is_approval_question("布洛芬 FDA 批准了吗？")
    assert is_adverse_event_question("布洛芬有什么不良反应？")
    assert is_health_topic_question("高血压是什么病？")
    assert is_clinical_trial_question("NCT02205112 的详情")
    assert is_research_question("PMID 40679934 的摘要")


def test_new_external_summaries():
    assert "共 35 条" in summarize_openfda_shortages({"text": SHORTAGE_TEXT})
    assert "共 208 条" in summarize_openfda_approvals({"text": APPROVAL_TEXT})
    assert "Dyspepsia" in summarize_openfda_adverse_events({"text": AE_TEXT})
    assert "High Blood Pressure" in summarize_medlineplus_search(MEDLINE_DATA)


def test_answer_shortage_formats(monkeypatch):
    monkeypatch.setattr(opening, "DEFAULT_CATALOG", _registered())
    monkeypatch.setattr(
        opening,
        "call_tool",
        lambda name, params: ToolResult(
            ok=True, name=name, data={"text": SHORTAGE_TEXT}, latency_ms=5
        ),
    )

    result = answer_shortage(_state("布洛芬短缺吗？"))

    text = result["rendered_text"]
    assert "Carboplatin Injection" in text
    assert "Limited Availability" in text
    assert "来源：openFDA" in text
    assert result["trace"][1]["called"] == "openfda_search_drug_shortages"


def test_answer_approval_formats(monkeypatch):
    monkeypatch.setattr(opening, "DEFAULT_CATALOG", _registered())
    monkeypatch.setattr(
        opening,
        "call_tool",
        lambda name, params: ToolResult(
            ok=True, name=name, data={"text": APPROVAL_TEXT}, latency_ms=5
        ),
    )

    result = answer_approval(_state("布洛芬 FDA 批准了吗？"))

    text = result["rendered_text"]
    assert "ANDA071265" in text
    assert "CONTRACT PHARMACAL" in text
    assert "Discontinued" in text


def test_answer_adverse_events_keeps_disclaimer(monkeypatch):
    monkeypatch.setattr(opening, "DEFAULT_CATALOG", _registered())
    monkeypatch.setattr(
        opening,
        "call_tool",
        lambda name, params: ToolResult(
            ok=True, name=name, data={"text": AE_TEXT}, latency_ms=5
        ),
    )

    result = answer_adverse_events(_state("布洛芬有什么不良反应？"))

    text = result["rendered_text"]
    assert "Dyspepsia" in text
    assert "不能证明因果" in text
    assert "来源：openFDA" in text


def test_answer_health_topic_maps_chinese(monkeypatch):
    monkeypatch.setattr(opening, "DEFAULT_CATALOG", _registered())
    monkeypatch.setattr(
        opening,
        "call_tool",
        lambda name, params: ToolResult(
            ok=True, name=name, data=MEDLINE_DATA, latency_ms=5
        ),
    )

    assert _health_topic_query("高血压是什么病？") == "hypertension"
    result = answer_health_topic(_state("高血压是什么病？"))

    text = result["rendered_text"]
    assert "High Blood Pressure" in text
    assert "MedlinePlus" in text
    assert "不构成诊断或治疗建议" in text


def test_trial_record_by_nct(monkeypatch):
    monkeypatch.setattr(opening, "DEFAULT_CATALOG", _registered())

    def fake_call(name, params):
        assert name == "clinicaltrials_get_study_record"
        return ToolResult(
            ok=True, name=name, data={"text": TRIAL_RECORD_TEXT}, latency_ms=5
        )

    monkeypatch.setattr(opening, "call_tool", fake_call)
    result = answer_clinical_trials(_state("NCT02205112 的详情"))

    text = result["rendered_text"]
    assert "NCT02205112" in text
    assert "COMPLETED" in text
    assert "Community-acquired Pneumonia" in text


def test_pubmed_article_by_pmid(monkeypatch):
    monkeypatch.setattr(opening, "DEFAULT_CATALOG", _registered())

    def fake_call(name, params):
        assert name == "pubmed_fetch_articles"
        return ToolResult(
            ok=True, name=name, data={"text": PUBMED_ARTICLE_TEXT}, latency_ms=5
        )

    monkeypatch.setattr(opening, "call_tool", fake_call)
    result = answer_research(_state("PMID 40679934 的摘要"))

    text = result["rendered_text"]
    assert "PubMed 文献" in text
    assert "BACKGROUND" in text
    assert "PMID：40679934" in text
    assert "DOI：10.1164/rccm.202507-1692ST" in text


def test_new_external_paths_disabled_return_none(monkeypatch):
    monkeypatch.setattr(opening, "DEFAULT_CATALOG", SimpleNamespace(get=lambda name: None))

    assert answer_shortage(_state("布洛芬短缺吗？")) is None
    assert answer_approval(_state("布洛芬 FDA 批准了吗？")) is None
    assert answer_adverse_events(_state("布洛芬有什么不良反应？")) is None
    assert answer_health_topic(_state("高血压是什么病？")) is None


def test_external_reply_translates(monkeypatch):
    monkeypatch.setattr(opening, "DEFAULT_CATALOG", _registered())
    monkeypatch.setattr(
        opening,
        "call_tool",
        lambda name, params: ToolResult(
            ok=True, name=name, data=MEDLINE_DATA, latency_ms=5
        ),
    )
    monkeypatch.setattr(
        opening,
        "localize_external",
        lambda text: "来源：MedlinePlus。高血压健康教育摘要。",
    )

    result = answer_health_topic(_state("高血压是什么病？"))

    assert result["rendered_text"] == "来源：MedlinePlus。高血压健康教育摘要。"
    assert result["trace"][0]["translated"] is True


def test_external_reply_rejects_unsafe_translation(monkeypatch):
    monkeypatch.setattr(opening, "DEFAULT_CATALOG", _registered())
    monkeypatch.setattr(
        opening,
        "call_tool",
        lambda name, params: ToolResult(
            ok=True, name=name, data={"text": OPENFDA_TEXT}, latency_ms=5
        ),
    )
    monkeypatch.setattr(
        opening,
        "localize_external",
        lambda text: "建议服用布洛芬，每次一片。来源：openFDA",
    )

    result = answer_drug_label(_state("布洛芬的说明书里有什么警示？"))

    assert "FDA 说明书" in result["rendered_text"]
    assert result["trace"][0]["translated"] is False


def test_translated_gate_allows_package_size_but_blocks_usage():
    assert translated_external_is_safe(
        "D-0583-2018（II 类）；布洛芬片 USP，100 片/瓶。来源：openFDA"
    )
    assert not translated_external_is_safe(
        "建议服用布洛芬，每次一片。来源：openFDA"
    )


def test_medlineplus_fetch_rejects_other_domains():
    assert _fetch_medlineplus_text("https://example.com/topic") == ""


def test_health_topic_uses_page_summary(monkeypatch):
    monkeypatch.setenv("USE_MODEL", "1")
    monkeypatch.setattr(opening, "DEFAULT_CATALOG", _registered())
    monkeypatch.setattr(
        opening,
        "call_tool",
        lambda name, params: ToolResult(
            ok=True, name=name, data=MEDLINE_DATA, latency_ms=5
        ),
    )
    monkeypatch.setattr(
        opening, "_fetch_medlineplus_text", lambda url: "Blood pressure is the force ..."
    )
    monkeypatch.setattr(
        opening,
        "chat",
        lambda *args, **kwargs: LLMResult(
            content="高血压是血液对血管壁压力持续升高。",
            model="qwen-plus",
            input_tokens=10,
            output_tokens=10,
            latency_ms=10,
            cost_estimate=None,
        ),
    )
    monkeypatch.setattr(opening, "localize_external", lambda text: text)

    result = answer_health_topic(_state("高血压是什么病？"))

    text = result["rendered_text"]
    assert "摘要：高血压是血液对血管壁压力持续升高。" in text
    assert "MedlinePlus 健康主题：高血压" in text
