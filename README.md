# ScholarAI

一站式论文调研 Agent：问答 + RAG + 多 agent 论文研究全流程。

## 特性
- **问答模式**：单 agent + 可挂载知识库（RAG / Chroma）
- **研究模式**：ReAct 主 agent 调度 5 个子 agent（检索 / 下载 / 解析 / 综述 / 审校）
- **知识库管理**：上传 PDF 自动索引，支持引用
- **可观测性**：LangSmith trace、Prometheus 指标、结构化日志（structlog）
- **部署**：docker-compose 一键启动，镜像自包含 BGE embedding 模型

## 快速开始

### 方式一：本地源码跑（dev 推荐）
```bash
make init-env        # 从 apikey.txt 读 key 写 backend/.env
make install-be      # 装 Python 依赖
make install-fe      # 装 Node 依赖

# 首次启动会自动执行数据库迁移

# 两个终端：
make up-be           # 后端 → http://localhost:8000
make up-fe           # 前端 → http://localhost:5173
```

### 方式二：Docker
```bash
make docker-build    # 首次 3-10 分钟（BGE 模型下载 + wheels）
make docker-up       # 启 → http://localhost:5173
make docker-logs
make docker-down
```
> Docker 镜像**自包含 BGE embedding 模型**（build 阶段预下载），无需运行时联网。

## 技术栈
- **前端**：React 19 + TypeScript + Vite + Tailwind + shadcn/ui
- **后端**：FastAPI + LangChain + LangGraph（ReAct agent）
- **LLM**：DeepSeek（dev）/ OpenAI / Anthropic / Qwen（生产可切）
- **Embedding**：本地 BGE-small-zh（dev）/ OpenAI / Qwen（生产可切）
- **向量库**：ChromaDB（嵌入式 PersistentClient，单 worker）
- **PDF 解析**：pdfplumber
- **数据库**：SQLite（dev）/ PostgreSQL（生产可切）
- **可观测性**：structlog + Prometheus + LangSmith

### 配置远程 embedding 服务

项目支持任何 OpenAI-compatible 的 `/v1/embeddings` 接口。编辑
`backend/.env`：

```env
EMBEDDING_PROVIDER=remote       # openai、qwen、remote 均可
EMBEDDING_API_KEY=your-key
EMBEDDING_BASE_URL=https://your-embedding-host/v1
EMBEDDING_MODEL=your-embedding-model
```

如果配置了远程 embedding 的 `EMBEDDING_BASE_URL`，必须同时设置
`EMBEDDING_PROVIDER=remote`（或 `openai` / `qwen`）；否则系统会按默认值使用本地 BGE。

`EMBEDDING_BASE_URL` 填到 `/v1`，不要填写完整的 `/v1/embeddings`。
`OPENAI_API_KEY`、`OPENAI_BASE_URL` 和 `OPENAI_EMBEDDING_MODEL` 也可作为兼容别名；Qwen DashScope 还支持 `DASHSCOPE_API_KEY`。
切换 embedding 模型后，服务下次启动会自动删除使用旧模型的知识库、向量和关联上传文件，请重新上传文档建立索引；相关提示也写在 `.env.example` 中。

如果直接运行 `uv run uvicorn`，先在 `backend` 目录执行 `uv run alembic upgrade head`。

## 健康检查
- `GET /health/live` — 进程存活（livenessProbe）
- `GET /health/ready` — DB + Chroma + 磁盘可写（readinessProbe，503 若失败）
- `GET /metrics` — Prometheus 抓取

## 路线图
- [x] M1 骨架
- [x] M2 知识库 + RAG
- [x] M3 研究模式 v1（abstract 综述）
- [x] M4 研究模式 v2（论文级综述）
- [x] M4.5 ReAct 主 agent 重构
- [x] M5 可观测性
- [x] M6 部署

## License
TBD
