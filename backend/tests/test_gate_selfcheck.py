"""Phase 3 门禁自检：追问上限被改大时，评测必须变红。"""

from eval.self_check import main


def test_followup_cap_gate_is_sensitive():
    assert main() == 0