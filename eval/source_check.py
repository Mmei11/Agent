"""Phase F：来源核验台账。

默认只做结构检查（来源存在、verified 有证据、没有项目自造来源）。
--strict 是发布门禁：要求所有条目 clinically_reviewed、所有 OTC 说明书
已由药师审核。
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

os.environ.setdefault("TRACE_SINK", "none")

DEFAULT_OUTPUT = Path("eval/results/source-check")
LABELS_PATH = Path("backend/app/drugs/labels.jsonl")


def _read_labels(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    from backend.app.knowledge.schema import load_entries, load_sources, validate_store

    errors: list[str] = []
    try:
        sources = load_sources()
        entries = load_entries()
        validate_store(entries)
    except Exception as exc:
        sources = {}
        entries = []
        errors.append(f"知识库校验失败：{exc}")

    if "src-project-derived" in sources:
        errors.append("仍引用项目自造来源 src-project-derived")

    for source in sources.values():
        if source.verified and not (source.url and source.retrieved_at):
            errors.append(f"来源 {source.id} 标记 verified 但缺少 URL/抓取日期")

    missing_sources = sorted(
        {entry.source_id for entry in entries if entry.source_id not in sources}
    )
    if missing_sources:
        errors.append("条目引用了不存在的来源：" + ", ".join(missing_sources))

    labels = _read_labels(LABELS_PATH)
    entry_status = Counter(entry.review_status for entry in entries)
    label_status = Counter(
        label.get("review_status", "pending") for label in labels
    )

    pending_entries = [
        entry.id for entry in entries if entry.review_status != "clinically_reviewed"
    ]
    pending_labels = [
        label.get("id", "")
        for label in labels
        if label.get("review_status") != "pharmacist_approved"
    ]

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "source-check-report.md"
    details_path = output_dir / "source-check-details.json"
    details = {
        "sources": len(sources),
        "entries": len(entries),
        "entry_status": dict(entry_status),
        "labels": len(labels),
        "label_status": dict(label_status),
        "errors": errors,
        "pending_entries": pending_entries,
        "pending_labels": pending_labels,
    }
    details_path.write_text(
        json.dumps(details, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lines = [
        "# 来源核验",
        "",
        f"- 来源：{len(sources)}",
        f"- 知识条目：{len(entries)}（{dict(entry_status)}）",
        f"- OTC 说明书：{len(labels)}（{dict(label_status)}）",
        f"- 结构错误：{len(errors)}",
        "",
    ]
    if errors:
        lines += ["## 错误", ""] + [f"- {item}" for item in errors]
    else:
        lines += ["结构检查通过。"]
    if pending_entries:
        lines += ["", "## 待临床审核条目", ""] + [
            f"- {item}" for item in pending_entries[:10]
        ]
        if len(pending_entries) > 10:
            lines.append(f"- 其余 {len(pending_entries) - 10} 条见明细")
    if pending_labels:
        lines += ["", "## 待药师审核说明书", ""] + [
            f"- {item}" for item in pending_labels[:10]
        ]
        if len(pending_labels) > 10:
            lines.append(f"- 其余 {len(pending_labels) - 10} 条见明细")
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(
        f"来源核验：{len(sources)} 个来源、{len(entries)} 条知识、"
        f"{len(labels)} 条说明书；结构错误 {len(errors)}；报告：{report_path}"
    )

    if errors:
        return 1
    if args.strict and (pending_entries or pending_labels):
        print(
            f"发布门禁未通过：待临床审核 {len(pending_entries)} 条、"
            f"待药师审核 {len(pending_labels)} 条"
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
