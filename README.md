# 智能问诊与症状分析 Agent

一个仅面向**虚构模拟病例**的求职演示项目，展示状态驱动追问、受限工具、风险门控、会话恢复和轨迹评测。它不提供真实医疗诊断、治疗或用药指导。

当前已完成 Day 16：后端 Agent、API 与 React 演示界面已实现；评测集包含 33 条虚构模拟案例，并提供单轮模型基线脚本。Day 1–3 的 `learning/` 是独立练习代码，不是产品入口。

- [产品规格](docs/spec.md)
- [模拟案例评测计划](docs/eval-plan.md)
- [开发进度](docs/progress.md)
- [20 天开发计划](医疗问诊Agent-20天开发计划.md)

## 技术栈与本地准备

Python 3.11、LangGraph、Pydantic 2、FastAPI、SQLite；前端计划使用 React + Vite + JavaScript。模型经单一 API 适配层接入，密钥仅存本地 `.env`。

安装后端开发依赖：

```sh
~/.local/bin/uv sync --locked
```

后续实现将位于 `backend/`；前端建成后单独提交 `package-lock.json`。依赖精确版本由 `uv.lock` 固定。
