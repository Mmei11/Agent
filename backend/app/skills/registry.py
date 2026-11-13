"""Phase D：技能注册表。

启动时只扫描 manifest，导出“名字 + 一句话描述 + 触发词”的路由摘要；
SKILL.md 在技能被选中后才读取，避免一次性把所有技能正文塞进上下文。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import yaml

from backend.app.skills.schema import SkillManifest

logger = logging.getLogger(__name__)

DEFAULT_NAME = "default"
DEFAULT_TITLE = "通用问诊"
DEFAULT_DESCRIPTION = "未命中特定病种时的通用问诊"

_MANIFESTS: dict[str, tuple[SkillManifest, Path]] | None = None


@dataclass(frozen=True)
class SkillSummary:
    """路由阶段能看到的全部信息；刻意不含 SKILL.md 正文。"""

    name: str
    title: str
    description: str
    priority: int
    when_to_use: tuple[str, ...]


@dataclass(frozen=True)
class Skill:
    manifest: SkillManifest
    instructions: str
    path: Path


def repo_root() -> Path:
    # backend/app/skills/registry.py → 仓库根目录
    return Path(__file__).resolve().parents[3]


def skills_dir() -> Path:
    return Path(os.getenv("SKILLS_DIR") or (repo_root() / "skills"))


def reset_skills_cache() -> None:
    global _MANIFESTS
    _MANIFESTS = None


def _fallback_manifest() -> SkillManifest:
    """技能目录缺失或 default 损坏时的最后兜底：等价于当前六字段行为。"""
    from backend.app.agent.followup import (
        FIELD_PRIORITY,
        QUESTION_TEMPLATES,
        REPLY_OPTIONS,
    )
    from backend.app.agent.intake import OPTIONAL_FIELDS, REQUIRED_FIELDS

    ordered = sorted(REQUIRED_FIELDS, key=lambda field: FIELD_PRIORITY.get(field, 99))
    return SkillManifest(
        name=DEFAULT_NAME,
        title=DEFAULT_TITLE,
        description=DEFAULT_DESCRIPTION,
        priority=999,
        when_to_use=[],
        required_fields=list(REQUIRED_FIELDS),
        optional_fields=list(OPTIONAL_FIELDS),
        followup_priority=ordered,
        question_templates=dict(QUESTION_TEMPLATES),
        reply_options={field: list(options) for field, options in REPLY_OPTIONS.items()},
        tools=["department_route", "red_flag_lookup", "guideline_retrieve"],
        output_template="建议就诊方向：{department}",
    )


def _load_manifests() -> dict[str, tuple[SkillManifest, Path]]:
    global _MANIFESTS
    if _MANIFESTS is not None:
        return _MANIFESTS

    enabled = os.getenv("SKILLS_ENABLED", "1") != "0"
    root = skills_dir()
    manifests: dict[str, tuple[SkillManifest, Path]] = {}

    if root.is_dir():
        for manifest_path in sorted(root.glob("*/manifest.yaml")):
            name = manifest_path.parent.name
            if not enabled and name != DEFAULT_NAME:
                continue
            try:
                payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
                manifest = SkillManifest.model_validate(payload)
            except Exception as exc:
                logger.warning("跳过无效技能 %s：%s", manifest_path, exc)
                continue

            if manifest.name != name:
                logger.warning(
                    "技能目录名与 manifest.name 不一致：%s vs %s", name, manifest.name
                )
                continue
            if name in manifests:
                logger.warning("技能重名，保留第一个：%s", name)
                continue
            manifests[name] = (manifest, manifest_path.parent)

    if DEFAULT_NAME not in manifests:
        manifests[DEFAULT_NAME] = (_fallback_manifest(), root / DEFAULT_NAME)

    _MANIFESTS = manifests
    return manifests


def list_skill_summaries() -> list[SkillSummary]:
    """路由用摘要：只有名字、描述和触发词，不读 SKILL.md。"""
    items: Iterable[tuple[SkillManifest, Path]] = _load_manifests().values()
    summaries = [
        SkillSummary(
            name=manifest.name,
            title=manifest.title,
            description=manifest.description,
            priority=manifest.priority,
            when_to_use=tuple(manifest.when_to_use),
        )
        for manifest, _ in items
    ]
    return sorted(summaries, key=lambda item: (item.priority, item.name))


def _read_instructions(path: Path) -> str:
    skill_file = path / "SKILL.md"
    try:
        return skill_file.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def load_skill(name: str) -> Skill:
    """技能被选中后才读 SKILL.md；读取失败回退 default。"""
    manifests = _load_manifests()
    selected = manifests.get(name) or manifests[DEFAULT_NAME]
    instructions = _read_instructions(selected[1])

    if not instructions and selected[0].name != DEFAULT_NAME:
        logger.warning("技能 %s 缺少 SKILL.md，回退 default", selected[0].name)
        selected = manifests[DEFAULT_NAME]
        instructions = _read_instructions(selected[1])

    return Skill(manifest=selected[0], instructions=instructions, path=selected[1])
