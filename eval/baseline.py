"""Single-turn model baseline for the fictional consultation evaluation set.

Each case's turns are concatenated into one user message. This intentionally
does not call the agent graph or its tools; it is a lightweight comparison, not
a medical-quality benchmark.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from urllib.request import Request, urlopen

from backend.app.config import load_env_file
from backend.app.llm.client import DEFAULT_BASE_URL


SYSTEM_PROMPT = (
    "你正在参与一个纯虚构的问诊软件演示。仅根据提供的信息做简短整理，"
    "明确区分已知和未知；不要作确定诊断、开药、给剂量或建议停药。"
    "如出现预设紧急风险词，提示寻求紧急专业帮助。不要声称这是医疗建议。"
)


def call_baseline(messages: list[str], timeout: float = 30.0) -> tuple[str, float]:
    """Call the configured DashScope-compatible chat model once."""
    load_env_file()
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError("缺少 DASHSCOPE_API_KEY；请配置 .env 或环境变量")
    base_url = os.getenv("DASHSCOPE_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    content = "\n".join(f"第{i}轮：{message}" for i, message in enumerate(messages, 1))
    payload = {
        "model": os.getenv("DASHSCOPE_MODEL", "qwen-plus"),
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
        "enable_thinking": False,
        "max_tokens": 512,
    }
    request = Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    with urlopen(request, timeout=timeout) as response:
        result = json.load(response)
    elapsed = time.perf_counter() - started
    return result["choices"][0]["message"]["content"], elapsed


def run(cases_path: Path, output_path: Path, limit: int | None = None) -> int:
    # 读取案例文件；splitlines() 按行拆分，跳过空白行，再逐行把 JSON 文本解析成字典。
    records = [json.loads(line) for line in cases_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    # 如果指定了 limit，只保留前 limit 条案例，常用于小批量试跑。
    if limit is not None:
        records = records[:limit]
    # 确保输出文件所在的目录存在；parents=True 会连上级目录一起创建。
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # 初始化成功计数；每成功调用一次模型，就加 1。
    completed = 0
    # 以写入模式打开结果文件；文件不存在时会创建，已存在时会覆盖。
    with output_path.open("w", encoding="utf-8") as output:
        # 逐个处理案例；case 是当前案例对应的字典。
        for case in records:
            # 先组装本条结果的基本信息，模型调用成功或失败后再补充结果字段。
            record = {
                # 保存案例 ID，方便把基线结果和案例集对应起来。
                "case_id": case["id"],
                # 记录使用的模型；未设置环境变量时用 qwen-plus。
                "model": os.getenv("DASHSCOPE_MODEL", "qwen-plus"),
                # 原样记录输入轮次，便于复核模型实际收到的案例内容。
                "input_turns": case["input_turns"],
                # 标记基线方式：多轮输入合并后只调用模型一次。
                "baseline_mode": "single_call_concatenated_turns",
            }
            try:
                # 把本案例所有轮次交给基线函数；拿到模型回复和耗时秒数。
                answer, elapsed = call_baseline(case["input_turns"])
                # 记录成功状态、模型回复、保留三位小数的耗时，以及一次模型调用。
                record.update(status="ok", answer=answer, latency_seconds=round(elapsed, 3), model_calls=1)
                # 成功案例数加一。
                completed += 1
            except Exception as exc:  # keep one failed request from discarding prior rows
                # 记录异常类型和说明，并继续处理后续案例，不让单条失败中断整个批次。
                record.update(status="error", error=f"{type(exc).__name__}: {exc}", model_calls=0)
            # 把结果字典编码成一行 JSON；ensure_ascii=False 保留中文字符。
            output.write(json.dumps(record, ensure_ascii=False) + "\n")
            # 立即刷新文件缓冲区，避免程序中途停止时丢失已写入的结果。
            output.flush()
    # 打印总案例数、成功数和结果文件路径。
    print(f"已处理 {len(records)} 条；成功 {completed} 条；结果：{output_path}")
    # 所有案例都成功时返回 0；只要有失败就返回 1，供命令行或自动化识别。
    return 0 if completed == len(records) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=Path("eval/cases.jsonl"))
    parser.add_argument("--output", type=Path, default=Path("eval/results/baseline-results.jsonl"))
    parser.add_argument("--limit", type=int, help="仅运行前 N 条，便于小批量核对")
    args = parser.parse_args()
    return run(args.cases, args.output, args.limit)


if __name__ == "__main__":
    raise SystemExit(main())
