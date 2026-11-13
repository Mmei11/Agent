"""技能与状态之间的薄适配层，避免各节点重复拼 default 逻辑。"""

from backend.app.skills.registry import DEFAULT_NAME, Skill, load_skill


def skill_for(state) -> Skill:
    return load_skill(getattr(state, "skill", "") or DEFAULT_NAME)
