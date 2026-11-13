"""Phase D：版本化 prompt 加载器。

约定路径：prompts/<name 用点分段>/<version>.txt，例如
prompts/intake/extract/v3.txt。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

_CACHE: dict[tuple[str, float], str] = {}
_VALID_PART = re.compile(r"^[a-z0-9_]+$")
_VALID_VERSION = re.compile(r"^v\d+$")


class PromptNotFound(FileNotFoundError):
    pass


def prompts_dir() -> Path:
    override = os.getenv("PROMPTS_DIR")
    if override:
        return Path(override)
    # backend/app/prompts.py → 仓库根目录
    return Path(__file__).resolve().parents[2] / "prompts"


def load_prompt(name: str, version: str) -> str:
    parts = name.split(".")
    if not parts or any(not _VALID_PART.match(part) for part in parts):
        raise ValueError(f"非法 prompt 名称：{name}")
    if not _VALID_VERSION.match(version):
        raise ValueError(f"非法 prompt 版本：{version}")

    path = prompts_dir().joinpath(*parts, f"{version}.txt")
    try:
        mtime = path.stat().st_mtime
    except OSError as exc:
        raise PromptNotFound(f"找不到 prompt 文件：{path}") from exc

    key = (str(path), mtime)
    if key in _CACHE:
        return _CACHE[key]

    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise PromptNotFound(f"prompt 文件为空：{path}")
    _CACHE[key] = text
    return text


def reset_prompt_cache() -> None:
    _CACHE.clear()
