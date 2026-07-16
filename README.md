# ScholarAI

一站式论文调研 Agent：问答 + RAG + 多 agent 论文研究全流程。

## 特性
- **问答模式**：单 agent + 可挂载知识库（RAG / Chroma）
- **研究模式**：多 agent 协同（规划 → arxiv 检索 → 下载 → pdfplumber 解析 → 综述）
- **知识库管理**：上传 PDF 自动索引，支持引用
- **可观测性**：LangSmith trace、Prometheus 指标、结构化日志
- **双部署**：本地 docker-compose + 云端托管

## 快速开始

```bash
# 1. 准备环境
make init-env        # 从 apikey.txt 读 key 写 backend/.env
make install-be      # 装 Python 依赖
make install-fe      # 装 Node 依赖

# 2. 启动（两个终端）
make up-be           # 后端 → http://localhost:8000
make up-fe           # 前端 → http://localhost:5173
```

## 技术栈
- **前端**：React 18 + TypeScript + Vite + Tailwind + shadcn/ui
- **后端**：FastAPI + LangChain + LangGraph
- **LLM**：DeepSeek（dev）/ OpenAI / Anthropic / Qwen（生产可切）
- **Embedding**：本地 BGE-small-zh（dev）/ OpenAI / Qwen（生产可切）
- **向量库**：ChromaDB
- **PDF 解析**：pdfplumber
- **数据库**：SQLite（dev）/ PostgreSQL（生产）

## 文档
- [IMPLEMENTATION_PLAN.md](./IMPLEMENTATION_PLAN.md) — 架构设计
- [DEVELOPMENT_PLAN.md](./DEVELOPMENT_PLAN.md) — 分阶段开发计划

## 路线图
- [x] M1 骨架
- [ ] M2 知识库 + RAG
- [ ] M3 研究模式 v1
- [ ] M4 研究模式 v2
- [ ] M5 可观测性
- [ ] M6 部署

## License
TBD
