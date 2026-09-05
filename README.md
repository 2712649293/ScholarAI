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
