# ScholarAI 前端

React 19、TypeScript、Vite、Tailwind；路由由 React Router 管理，Markdown 使用 react-markdown / remark-gfm。项目配置与后端启动见[根 README](../README.md)。

## 开发命令

使用 Node.js 22（≥22.13）或 24 LTS，在 `frontend/` 执行：

```bash
npm ci              # 按 package-lock.json 安装
npm run dev         # http://localhost:5173
npm run build       # TypeScript 检查 + 构建到 dist/
npm run lint        # Oxlint
npm test            # Vitest，jsdom + Testing Library
```

所有 API 请求使用相对路径 `/api/*`，开发代理在 [vite.config.ts](vite.config.ts) 中指向 `http://localhost:8000`。`npm run preview` 仅预览构建产物，未配置 API 代理；完整部署见[部署说明](../deploy/README.md)。

## 入口与交互

| 位置 | 职责 |
| --- | --- |
| `src/App.tsx`、`pages/` | `/chat`、`/chat/:sessionId`、`/knowledge`、`/knowledge/:id` |
| `src/components/ChatPanel.tsx` | 问答、知识库选择、研究澄清 / 计划 / 执行 / 追问和报告导出 |
| `src/components/PlanCard.tsx`、`ResearchProgress.tsx` | 计划操作和研究步骤展示 |
| `src/components/Sidebar.tsx` | 会话列表、切换与删除 |
| `src/lib/api.ts` | JSON 请求、接口类型和基于 fetch 的 GET / POST SSE 解析 |

修改接口时同步核对后端 schema、`src/lib/api.ts` 和组件事件处理。研究进度使用 SSE 步骤事件，当前并非逐 token 输出。批准计划后的执行和追问由后端任务运行；切换会话仅断开订阅，切回后自动重连并重放本次任务进度。连接错误可重新连接；服务重启或研究失败且有待执行检查点时显示“继续未完成的研究”，点击后才恢复，不重新批准计划。最终消息按持久化消息 ID 去重；连接丢失但后台已完成时重新加载历史。澄清与计划生成仍采用原请求生命周期。

知识库详情提供文档重新处理与删除、可选图片处理警告；检索和问答引用展示页码范围、章节及图号，融合分数与重排分数分别显示。后端 RAG 配置见 [RAG 使用说明](../docs/rag.md)。
