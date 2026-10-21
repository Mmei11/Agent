# 智能问诊 Agent 前端

React + Vite 单页界面，仅面向虚构模拟病例演示。

## 开发

```sh
cd frontend
npm install
npm run dev
```

开发服务器启动后，访问 http://localhost:5173。后端 API 需同时运行在 http://localhost:8000（`uvicorn backend.app.api.routes:app --reload`）。

Vite 配置了代理，前端 `/sessions` 请求自动转发到后端 `http://localhost:8000`。

## 代码格式化

使用 Prettier 统一格式化 JSX、CSS 和配置文件：

```sh
npm run format
npm run format:check
```

规则在 `.prettierrc.json` 中，当前每行建议宽度为 88 字符。

## 功能

- 输入框输入症状描述
- 每次演示场景开启独立会话；风险或普通结果结束后，再输入会自动开启新会话
- 每轮最多一个追问，对话记录随 SQLite 检查点保存，刷新页面可恢复
- 状态区显示已收集、尚不确定、追问次数、风险标记
- 结果卡片展示结构化最终结果
- 3 个模拟案例快捷入口
- session_id 存 localStorage，30 分钟过期；“重新开始”会删除对应的 SQLite 检查点
