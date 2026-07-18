# ScholarAI

一站式论文调研 Agent：问答 + RAG + 多 agent 论文研究全流程。

## 特性
- **问答模式**：单 agent + 可挂载知识库（RAG / Chroma）
- **研究模式**：ReAct 主 agent 调度 5 个子 agent（检索 / 下载 / 解析 / 综述 / 审校）
- **知识库管理**：上传 PDF 自动索引，支持引用
- **可观测性**：LangSmith trace、Prometheus 指标、结构化日志（structlog）
- **双部署**：本地 docker-compose + 云端托管

## 快速开始

### 方式一：本地源码跑（dev 推荐）
```bash
make init-env        # 从 apikey.txt 读 key 写 backend/.env
make install-be      # 装 Python 依赖
make install-fe      # 装 Node 依赖

# 两个终端：
make up-be           # 后端 → http://localhost:8000
make up-fe           # 前端 → http://localhost:5173
```

### 方式二：Docker（生产形态 / 5 分钟拉起）
```bash
make init-env        # 先准备 .env（docker 镜像读这个）
make docker-build    # build 镜像（首次会拉基础镜像 + 下载 BGE 模型）
make docker-up       # 启 → http://localhost:5173
make docker-logs     # 看日志
make docker-down     # 停
```

> Docker 镜像**自包含 BGE embedding 模型**（build 阶段预下载），无需运行时联网。
> 单 worker 部署（嵌入式 Chroma 限制）；多 worker 扩展见 [DEVELOPMENT_PLAN.md §12.10](./DEVELOPMENT_PLAN.md)。

## 技术栈
- **前端**：React 19 + TypeScript + Vite + Tailwind + shadcn/ui
- **后端**：FastAPI + LangChain + LangGraph（ReAct agent）
- **LLM**：DeepSeek（dev）/ OpenAI / Anthropic / Qwen（生产可切）
- **Embedding**：本地 BGE-small-zh（dev）/ OpenAI / Qwen（生产可切）
- **向量库**：ChromaDB（嵌入式 PersistentClient，单 worker）
- **PDF 解析**：pdfplumber
- **数据库**：SQLite（dev）/ PostgreSQL（生产可切）
- **可观测性**：structlog + Prometheus + LangSmith

## 健康检查
- `GET /health/live` — 进程存活（livenessProbe）
- `GET /health/ready` — DB + Chroma + 磁盘可写（readinessProbe，503 若失败）
- `GET /metrics` — Prometheus 抓取

## 文档
- [IMPLEMENTATION_PLAN.md](./IMPLEMENTATION_PLAN.md) — 架构设计
- [DEVELOPMENT_PLAN.md](./DEVELOPMENT_PLAN.md) — 分阶段开发计划
- [deploy/README.md](./deploy/README.md) — 云端部署选项（Fargate / Cloud Run / SAE）

## 路线图
- [x] M1 骨架
- [x] M2 知识库 + RAG
- [x] M3 研究模式 v1（abstract 综述）
- [x] M4 研究模式 v2（论文级综述）
- [x] M4.5 ReAct 主 agent 重构
- [x] M5 可观测性
- [x] M6 部署（本地 Docker 完整；云端文档 v1.0 之前）

## 下一阶段
- v0.2 优先：研究模式**聊天追问（跨轮记忆）**——见 DEVELOPMENT_PLAN §12.11
- v1.0 之前：云端三选一部署文档（Fargate / Cloud Run / 阿里云 SAE）

## License
TBD
