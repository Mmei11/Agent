"""Phase A：M6 上下文保持率的判定逻辑。

这里只测“四层怎么算”，不启动 FastAPI、不调用模型。
端到端测量由 eval.context_retention 负责。
"""

from eval.context_retention import answer_path, contains_all, evaluate


def test_contains_all_requires_every_value():
    assert contains_all("我下周要出差", ["下周", "出差"])
    assert not contains_all("我下周要出差", ["下周", "出国"])


def test_evaluate_separates_facts_window_and_answer():
    case = {"expected_values": ["出差"], "expect_in_facts": False}
    values = {
        "collected_facts": {},
        "conversation": [{"role": "user", "text": "我下周要出差"}],
        "latest_user_message": "我刚才说之后要做什么？",
    }
    hits = evaluate(case, values, "你刚才说下周要出差。")

    assert hits["state_hit"] is False
    assert hits["window_hit"] is True
    assert hits["prompt_hit"] is True
    assert hits["answer_hit"] is True
    assert hits["fact_leak"] is False


def test_evaluate_marks_conversation_detail_as_leak_when_extracted():
    case = {"expected_values": ["出差"], "expect_in_facts": False}
    values = {
        "collected_facts": {"已知背景": ["下周要出差"]},
        "conversation": [{"role": "user", "text": "我下周要出差"}],
        "latest_user_message": "我刚才说之后要做什么？",
    }
    hits = evaluate(case, values, "")

    assert hits["state_hit"] is True
    assert hits["fact_leak"] is True


def test_evaluate_drops_detail_outside_recent_window():
    case = {"expected_values": ["出差"], "expect_in_facts": False}
    filler = [{"role": "user", "text": "无关追问"} for _ in range(6)]
    values = {
        "collected_facts": {},
        "conversation": [{"role": "user", "text": "我下周要出差"}, *filler],
        "latest_user_message": "我刚才说之后要做什么？",
    }
    hits = evaluate(case, values, "想不起来了。")

    assert hits["window_hit"] is False
    assert hits["prompt_hit"] is False
    assert hits["answer_hit"] is False


def test_answer_hit_rejects_denial_even_if_value_appears():
    case = {"expected_values": ["上午"], "expect_in_facts": False}
    values = {
        "collected_facts": {},
        "conversation": [{"role": "user", "text": "我更喜欢上午去"}],
        "latest_user_message": "我刚才说更喜欢哪个时间段去？",
    }
    answer = "您之前没有提到过具体的时间偏好哦，可以说上午、下午还是晚上吗？"
    hits = evaluate(case, values, answer)

    assert hits["recall_denied"] is True
    assert hits["answer_hit"] is False


def test_evaluate_uses_pre_probe_context_snapshot():
    """窗口和 prompt 只看探针之前的历史，不能被探针自己的回答点亮。"""
    case = {"expected_values": ["地铁"], "expect_in_facts": False}
    final_values = {
        "collected_facts": {},
        "conversation": [{"role": "agent", "text": "一般建议选择地铁"}],
        "latest_user_message": "怎么过来方便？",
    }
    context_values = {
        "collected_facts": {},
        "conversation": [{"role": "user", "text": "无关"} for _ in range(6)],
        "latest_user_message": "怎么过来方便？",
    }

    hits = evaluate(
        case,
        final_values,
        "一般建议选择地铁",
        context_values=context_values,
    )

    assert hits["window_hit"] is False
    assert hits["prompt_hit"] is False
    assert hits["answer_hit"] is True


def test_answer_path_distinguishes_router_bypass():
    router_values = {
        "trace": [{"node": "answer", "source": "general", "general_answer_source": "router"}]
    }
    assert answer_path(router_values) == ("general", False)

    context_values = {"trace": [{"node": "answer", "source": "model"}]}
    assert answer_path(context_values) == ("model", True)

    assert answer_path({}) == ("", False)
