"""技能与状态之间的薄适配层，避免各节点重复拼 default 逻辑。"""

from backend.app.skills.registry import DEFAULT_NAME, Skill, load_skill


def skill_for(state) -> Skill:
    return load_skill(getattr(state, "skill", "") or DEFAULT_NAME)


def allowed_tools(state) -> set[str]:
    """当前技能允许 decision 自动选择的工具名。"""
    return set(skill_for(state).manifest.tools)
