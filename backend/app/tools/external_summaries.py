"""外部 MCP 工具的返回摘要与解析。

openFDA 和 PubMed 的 server 返回 Markdown 文本，通用的 JSON 截断只能看到
标题，进 decision 上下文没有信息量。这里按它们的已知格式提取信号：

- openFDA drug label：药名、产品类型、Set ID、警示/禁忌段落
- PubMed search：命中数、标题、PMID/DOI

摘要只进模型上下文；原始文本仍完整留在 state.tool_results 里。
"""

from __future__ import annotations

import re

from backend.app.tools.contract import clip_summary

_SECTION_RE = re.compile(r"^\*\*(.+?):\*\*\s*$")
_META_RE = re.compile(r"^\*\*(.+?):\*\*\s*(.+)$")
_DOSE_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:mg|ml|g|mcg|tablet|tablets|capsule|capsules|dose|doses|pill|pills)\b"
    r"|\d+\s*(?:片|粒|毫克|毫升|克)",
    re.IGNORECASE,
)


def markdown_text(data) -> str:
    if isinstance(data, dict):
        return str(data.get("text") or "")
    return ""


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _strip_section_prefix(name: str, text: str) -> str:
    """段落正文常以标题词开头（Warnings / Uses），显示时去掉重复。"""
    prefixes = [name]
    if name.startswith("Indications"):
        prefixes.append("Uses")
    for prefix in prefixes:
        if prefix and text.lower().startswith(prefix.lower() + " "):
            return text[len(prefix) + 1 :].strip()
    return text


def markdown_sections(text: str) -> dict[str, str]:
    """按 `**Section:**` 标题切段，返回 section → 段落正文。"""
    sections: dict[str, str] = {}
    current: str | None = None
    buffer: list[str] = []

    for line in text.splitlines():
        stripped = line.strip()
        match = _SECTION_RE.match(stripped)
        if match:
            if current is not None:
                sections[current] = _strip_section_prefix(
                    current, _clean(" ".join(buffer))
                )
            current = match.group(1).strip()
            buffer = []
            continue
        if current is not None:
            buffer.append(stripped)

    if current is not None:
        sections[current] = _strip_section_prefix(current, _clean(" ".join(buffer)))
    return sections


def parse_openfda_label(data) -> dict:
    """拆出标题、元信息与段落；不解析就不用碰 Markdown 细节。"""
    text = markdown_text(data)
    title = ""
    meta: dict[str, str] = {}

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("### "):
            title = stripped[4:].strip()
            continue
        match = _META_RE.match(stripped)
        if match:
            meta[match.group(1).strip()] = match.group(2).strip()

    return {"title": title, "meta": meta, "sections": markdown_sections(text)}


def safe_snippet(text: str, limit: int) -> str:
    """去掉含剂量的句子再截断；剂量不进任何对外文案。"""
    sentences = [part.strip() for part in re.split(r"(?<=[.!?。！？])\s+", text) if part.strip()]
    kept = [sentence for sentence in sentences if not _DOSE_RE.search(sentence)]
    joined = " ".join(kept)
    if len(joined) <= limit:
        return joined
    cut = joined[:limit]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip() + "…"


def pubmed_counts(text: str) -> tuple[str, str]:
    """兼容两种输出：Total Found/Returned 与 Returned: N of M。"""
    total = re.search(r"Total Found:\*\*\s*(\d+)", text) or re.search(
        r"Returned:\*\*\s*\d+\s*of\s*(\d+)", text
    )
    returned = re.search(r"Returned:\*\*\s*(\d+)", text)
    return (
        total.group(1) if total else "?",
        returned.group(1) if returned else "?",
    )


def summarize_openfda_label(data) -> str:
    parsed = parse_openfda_label(data)
    if not parsed["title"]:
        return clip_summary(markdown_text(data) or "FDA 说明书：未返回内容。")

    meta = parsed["meta"]
    parts = [f"FDA说明书：{parsed['title']}"]
    if meta.get("Product type"):
        parts.append(meta["Product type"])
    if meta.get("Set ID"):
        parts.append(f"Set ID {meta['Set ID'].split()[0]}")
    if meta.get("Effective date"):
        parts.append(f"生效 {meta['Effective date']}")

    sections = parsed["sections"]
    warnings = sections.get("Warnings") or sections.get("Boxed warning") or ""
    if warnings:
        parts.append("警示：" + safe_snippet(warnings, 180))

    return clip_summary("；".join(parts))


def summarize_openfda_recalls(data) -> str:
    text = markdown_text(data)
    return clip_summary(_clean(text) or "FDA 召回：未返回内容。")


def summarize_pubmed_search(data) -> str:
    text = markdown_text(data)
    total, returned = pubmed_counts(text)
    titles = re.findall(r"^####\s+(.+)$", text, re.MULTILINE)

    parts = [f"PubMed：共 {total} 条，返回 {returned if returned != '?' else len(titles)} 条"]
    if titles:
        parts.append("；".join(title.strip() for title in titles[:3]))
    else:
        pmids = re.search(r"\*\*PMIDs:\*\*\s*(.+)", text)
        if pmids:
            parts.append("PMID：" + pmids.group(1).strip())
    return clip_summary("；".join(parts))


def summarize_pubmed_fetch(data) -> str:
    text = markdown_text(data)
    titles = re.findall(r"^###\s+(.+)$", text, re.MULTILINE)
    pmids = re.findall(r"\*\*PMID:\*\*\s*(\d+)", text)
    if not titles:
        return clip_summary(_clean(text) or "PubMed：未返回文献。")
    parts = ["PubMed文献：" + "；".join(title.strip() for title in titles[:3])]
    if pmids:
        parts.append("PMID：" + ", ".join(pmids[:3]))
    return clip_summary("；".join(parts))


SUMMARIZERS = {
    "openfda_get_drug_label": summarize_openfda_label,
    "openfda_search_recalls": summarize_openfda_recalls,
    "pubmed_search_articles": summarize_pubmed_search,
    "pubmed_fetch_articles": summarize_pubmed_fetch,
}
