"""Phase C 第 4 课：工具结果摘要。

原始结果留在 state 里给渲染和审计；只有进 prompt 的那一份被压缩。
"""

from backend.app.agent.decision_llm import build_context
from backend.app.agent.state import ConsultationState
from backend.app.tools.catalog import prompt_tool_results
from backend.app.tools.contract import TOOL_SUMMARY_MAX_CHARS
from backend.app.tools.knowledge_tools import (
    summarize_department,
    summarize_guideline,
    summarize_red_flag,
)


def _hit(
    *,
    title: str = "头痛分诊原则",
    content: str = "以头痛为主诉时，结合部位与伴随症状判断就诊方向。",
    department: str = "神经内科",
    verified: bool = False,
    score: float = 0.82,
) -> dict:
    return {
        "id": "K001",
        "title": title,
        "content": content,
        "department": department,
        "scope_text": "成年人",
        "version": 1,
        "score": score,
        "matched_by": "vector",
        "source": {"id": "src-project-derived", "title": "项目归纳", "verified": verified},
    }


def test_department_summary_keeps_direction_and_source():
    summary = summarize_department(
        {"department": "神经内科", "hits": [_hit(), _hit(title="第二条")]}
    )

    assert "神经内科" in summary
    assert "项目归纳（未核实）" in summary
    assert "命中 2 条" in summary
    # 正文留在原始结果里，不进摘要
    assert "以头痛为主诉" not in summary


def test_department_summary_marks_no_match():
    assert "no_match" in summarize_department({"department": None, "hits": []})


def test_red_flag_summary_preserves_uncertainty():
    assert "不等于安全" in summarize_red_flag({"hits": []})

    summary = summarize_red_flag({"hits": [_hit(title="呼吸困难需急诊")]})
    assert "呼吸困难需急诊" in summary
    assert "不改变风险等级" in summary


def test_guideline_summary_keeps_at_most_three_points():
    hits = [
        _hit(title=f"准备事项{i}", content=f"第{i}条：带好身份证和既往检查资料。")
        for i in range(1, 6)
    ]

    summary = summarize_guideline({"hits": hits})

    assert "第1条" in summary and "第3条" in summary
    assert "第4条" not in summary


def test_every_summary_respects_char_budget():
    long_hit = _hit(title="很长的标题" * 100, content="很长的正文。" * 200)
    cases = [
        summarize_department({"department": "神经内科", "hits": [long_hit] * 3}),
        summarize_red_flag({"hits": [long_hit] * 3}),
        summarize_guideline({"hits": [long_hit] * 3}),
    ]

    for summary in cases:
        assert len(summary) <= TOOL_SUMMARY_MAX_CHARS
        assert summary.endswith("…")


def test_prompt_tool_results_summarizes_failures():
    summaries = prompt_tool_results(
        {"department_route": {"error": "执行超过 8.0s", "error_kind": "timeout"}}
    )

    assert summaries["department_route"].startswith("调用失败（timeout）")
    assert len(summaries["department_route"]) <= TOOL_SUMMARY_MAX_CHARS


def test_prompt_tool_results_falls_back_to_clipped_json():
    summaries = prompt_tool_results(
        {"external_tool": {"payload": "外部工具没有摘要函数。" * 100}}
    )

    assert len(summaries["external_tool"]) <= TOOL_SUMMARY_MAX_CHARS
    assert summaries["external_tool"].endswith("…")


def test_decision_context_sees_summary_not_raw_hits():
    raw = {"department": "神经内科", "hits": [_hit(content="不得进入 prompt 的正文" * 100)]}
    state = ConsultationState(tool_results={"department_route": raw})

    context = build_context(state, max_followups=3)

    assert len(context["tool_results"]["department_route"]) <= TOOL_SUMMARY_MAX_CHARS
    assert "不得进入 prompt 的正文" not in str(context["tool_results"])
    # 原始结果没有被摘要覆盖，渲染层仍能拿到完整字段
    assert state.tool_results["department_route"]["hits"][0]["department"] == "神经内科"
