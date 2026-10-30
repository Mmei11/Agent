"""知识条目的结构与加载。

两个字段刻意分开：
- scope_payload 给机器过滤用，字段必须是可判定的值；
- scope_text 给人看，也是 judge 判断"适用边界是否越界"的依据。

Source 的 verified 为 False 表示这条知识尚未逐条核对原文。检索结果必须把这个
状态带给下游，不能在输出时假装它已核实。
"""

import json  # 读 sources.json 和 entries.jsonl
from pathlib import Path  # 定位数据文件
from typing import Literal  # 限定取值范围，写错在加载时就报错

from pydantic import BaseModel, ConfigDict, Field

# 数据文件与本文件同级。挪动目录结构时这里要跟着改
DATA_DIR = Path(__file__).resolve().parent


class Source(BaseModel):
    """一份出处。verified 表示内容是否已逐条核对过原文。"""

    # extra="forbid"：多写一个字段直接报错。
    # 不加的话拼错字段名会被静默忽略，出处信息就悄悄丢了
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)  # 引用名，entries 里的 source_id 指向它
    title: str = Field(min_length=1)  # 资料名，如"科室分诊通用原则"
    issuer: str = Field(min_length=1)  # 发布方：谁说的
    year: int = Field(ge=1990, le=2100)  # 年份；限定区间可挡住漏填和手滑
    note: str = ""  # 补充说明，如"条目为项目归纳，非原文引用"
    verified: bool = False  # 默认未核实：宁可标出来，不假装已核实


class ScopePayload(BaseModel):
    """机器可过滤的适用范围。未知维度留空，表示不参与过滤。"""

    model_config = ConfigDict(extra="forbid")

    # 症状簇：两用。既做关键词检索的匹配词，也做向量库的过滤条件
    symptom_cluster: list[str] = Field(default_factory=list)
    # 风险等级：用来实现"红旗优先"这类过滤；None 表示不区分
    risk_level: Literal["low", "medium", "high"] | None = None
    # 年龄区间：用户年龄未知时不填，该维度就不拼进 WHERE 条件
    age_min: int | None = Field(default=None, ge=0, le=120)
    age_max: int | None = Field(default=None, ge=0, le=120)


class KnowledgeEntry(BaseModel):
    """一条知识：说什么、依据是什么、对谁成立。"""

    model_config = ConfigDict(extra="forbid")

    # 稳定标识：内容改了 id 也不变，这样 trace 里的历史记录能对上
    id: str = Field(min_length=1)
    # 类别：决定检索时走哪个工具（分诊 / 红旗 / 就医准备）
    category: Literal["department_route", "red_flag", "visit_prep"]
    title: str = Field(min_length=1)  # 简短标题，展示用
    # 正文：真正参与向量化和关键词匹配的那段文字
    content: str = Field(min_length=1)
    # 指向 sources.json 里的 id，不直接写文件名
    source_id: str = Field(min_length=1)
    scope_payload: ScopePayload  # 机器可过滤的适用范围
    scope_text: str = Field(min_length=1)  # 人读的适用边界
    version: str = Field(pattern=r"^v\d+$")  # 知识版本，如 v1、v2


def _read_jsonl(path: Path) -> list[dict]:
    """逐行读 JSONL。空行跳过，坏行带上行号报错。"""
    rows = []
    # 从 1 开始编号，报错时行号和人看文件时一致
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:  # 空行不是错误，跳过
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            # 必须带文件名和行号，否则几百行里报错等于没报
            raise ValueError(f"{path.name}:{line_no} 不是合法 JSON：{exc}") from exc
    return rows


def load_sources(path: Path | None = None) -> dict[str, Source]:
    """读取出处表，按 id 建索引。"""
    # 参数留空就读默认文件；测试时传入临时路径
    target = path or DATA_DIR / "sources.json"
    raw = json.loads(target.read_text(encoding="utf-8"))
    sources = [Source(**item) for item in raw]  # 逐条过一遍 Schema 校验
    # 返回 dict 而不是 list：后面拿 source_id 查出处是 O(1)
    return {source.id: source for source in sources}


def load_entries(path: Path | None = None) -> list[KnowledgeEntry]:
    """读取知识条目。校验留给 validate_store，这里只做解析。"""
    target = path or DATA_DIR / "entries.jsonl"
    # 单条合法性由 KnowledgeEntry 自己保证；条目之间的关系由 validate_store 查
    return [KnowledgeEntry(**row) for row in _read_jsonl(target)]


def validate_store(
    entries: list[KnowledgeEntry] | None = None,
    sources: dict[str, Source] | None = None,
) -> list[str]:
    """交叉校验，返回问题列表；空列表表示通过。

    这里只查结构与引用完整性，不判断医学内容是否正确。
    """
    # 两个参数都可以省略，省略就读磁盘上的真实数据
    entries = load_entries() if entries is None else entries
    sources = load_sources() if sources is None else sources
    problems: list[str] = []  # 收集所有问题，而不是遇到第一个就抛异常

    seen: set[str] = set()  # 用来发现重复 id
    for entry in entries:
        if entry.id in seen:
            problems.append(f"条目 id 重复：{entry.id}")
        seen.add(entry.id)

        # 悬空引用：条目指向的出处不存在，检索时就拿不到 source
        if entry.source_id not in sources:
            problems.append(f"{entry.id} 引用了不存在的出处：{entry.source_id}")

        # 没有症状簇就既过滤不了、也做不了关键词匹配
        if not entry.scope_payload.symptom_cluster:
            problems.append(f"{entry.id} 的 symptom_cluster 为空，无法参与检索")

        # 只有两头都填了才比较；只填一头表示"只设下限/上限"，合法
        if (
            entry.scope_payload.age_min is not None
            and entry.scope_payload.age_max is not None
            and entry.scope_payload.age_min > entry.scope_payload.age_max
        ):
            problems.append(f"{entry.id} 的年龄区间颠倒")

    return problems