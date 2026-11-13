"""Phase D：图内技能路由节点。

general_route 判定为医疗后进入本节点：
- 首次进入时按消息路由技能，之后保持 sticky；
- 用技能 manifest 的 required_fields 重算 missing_fields；
- SKILL.md 不在这里读取，followup/decision 通过 skill_for 按需加载。
"""

from backend.app.agent.intake import compute_missing
from backend.app.agent.state import ConsultationState
from backend.app.observability import current_trace
from backend.app.skills.registry import load_skill
from backend.app.skills.router import route


def skill_route(state: ConsultationState) -> dict:
    if state.skill:
        source = "sticky"
        requested = state.skill
    else:
        source = "router"
        requested = route(state.latest_user_message)

    skill = load_skill(requested)
    manifest = skill.manifest
    missing = compute_missing(state.collected_facts, manifest.required_fields)
    scope = current_trace()

    return {
        "skill": manifest.name,
        "missing_fields": missing,
        "trace": [
            {
                "node": "skill_route",
                "skill": manifest.name,
                "source": source,
                "missing_count": len(missing),
                "required_count": len(manifest.required_fields),
                "trace_id": scope.trace_id if scope else "",
            }
        ],
    }
