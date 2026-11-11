"""Phase A：记忆指代识别。"""

import pytest

from backend.app.agent.memory_ref import is_memory_reference


@pytest.mark.parametrize(
    "text",
    [
        "我刚才说想什么时候去？",
        "你记得我提到的安排吗",
        "之前提到的那个偏好是什么",
        "我说过什么",
        "刚才那句话是什么",
        "我提的是哪里",
        "您还记得我说的时间吗",
        "我最早说的部位是什么",
    ],
)
def test_recognizes_memory_reference(text):
    assert is_memory_reference(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "那要注意什么",
        "需要空腹吗",
        "我今天头痛",
        "前面的牙有点疼",
        "大概多久能好",
        "",
    ],
)
def test_does_not_match_ordinary_followups(text):
    assert is_memory_reference(text) is False
