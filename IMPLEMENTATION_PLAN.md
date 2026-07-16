# ScholarAI · 一站式论文调研 Agent · 实施方案

> 状态：v0.1 设计稿 ｜ 2026-07-16
> 目标：从 0 到 1 实现「问答 + 论文研究」双模式 Agent 平台，支持本地与云端部署。

---

## 1. 需求拆解

| # | 需求 | 关键点 |
|---|------|--------|
| 1 | 论文调研全流程 | arxiv 检索 → PDF 下载 → pdfplumber 解析 → 综述生成 |
| 2 | 双模式对话 | 问答模式（单 agent + RAG） / 研究模式（多 agent 协同） |
| 3 | 知识库管理 | 用户自建知识库，ChromaDB 持久化，支持挂载到问答模式 |
| 4 | 对话记录 | 多会话持久化，可回看 |
| 5 | 可观测性 | Trace、日志、指标、调试入口 |
| 6 | 双部署 | 本地 docker-compose + 云端 K8s/容器服务 |
| 7 | 架构约束 | LangChain + LangGraph，React 前端 |

---

## 2. 整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                    React Frontend (Vite + TS)               │
│  Sidebar: [新对话] [对话历史] [知识库]   Main: 问答/研究模式  │
└──────────────────────────┬──────────────────────────────────┘
                           │ REST + SSE (流式)
┌──────────────────────────▼──────────────────────────────────┐
│                     FastAPI Gateway                         │
│  /chat  /research  /knowledge  /sessions  /metrics          │
└──────┬─────────────────────┬─────────────────────┬──────────┘
       │                     │                     │
┌──────▼──────┐      ┌───────▼────────┐    ┌───────▼────────┐
│  Q&A Agent  │      │ Research Graph │    │  Knowledge Svc │
│ (单 ReAct)  │      │  (LangGraph)   │    │  (Chroma)      │
└──────┬──────┘      └───────┬────────┘    └───────┬────────┘
       │                     │                     │
       └──────────────┬──────┴─────────────────────┘
                      │
        ┌─────────────┼─────────────┐
        │             │             │
   ┌────▼────┐  ┌─────▼─────┐  ┌────▼────┐
   │ arxiv   │  │ pdfplumber│  │  LLM   │
   │ API     │  │ PDF 解析  │  │ Provider│
   └─────────┘  └───────────┘  └─────────┘

   旁路：LangSmith/OTel trace、Prometheus metrics、structlog
```

---

## 3. 技术栈

### 3.1 前端
- **框架**：React 18 + TypeScript + Vite
- **路由**：React Router v6
- **状态**：Zustand（轻量，足够；不用 Redux）
- **UI**：shadcn/ui + TailwindCSS（直接 copy 组件，避免重造）
- **请求**：fetch + 自写 SSE 解析器（研究模式流式进度推送）；普通请求用 SWR 缓存
- **Markdown 渲染**：react-markdown + remark-gfm（综述报告展示）

> ponytail: 不引入 axios / antd / Redux。一个 SSE hook + fetch 就够。

### 3.2 后端
- **Web 框架**：FastAPI + uvicorn
- **Agent 框架**：LangChain + LangGraph（StateGraph）
- **LLM 抽象**：`langchain.chat_models` 统一接口，支持 OpenAI / Anthropic / Ollama（通过 env 切换）
- **向量库**：ChromaDB（HTTP 模式，便于多 worker 共享）
- **PDF 解析**：pdfplumber（主）+ PyMuPDF（兜底，处理扫描页）
- **arxiv**：`arxiv` Python SDK
- **持久化**：
  - 对话/会话：SQLite（默认，单机够用）+ 可切换 Postgres
  - 论文 PDF：本地文件系统（默认）或 MinIO/S3
- **任务队列**（可选）：研究模式长任务用 BackgroundTasks + SSE 推送；高并发时换 Celery + Redis
- **可观测性**：
  - `langsmith` SDK（如果用 LangSmith）
  - `langfuse`（自托管替代）
  - `structlog` 结构化日志
  - `prometheus-client` 暴露 `/metrics`
  - `opentelemetry-instrumentation-fastapi` 自动 trace

### 3.3 部署
- **本地**：`docker-compose.yml`（frontend / backend / chroma / postgres 可选 / minio 可选）
- **云端**：
  - 容器镜像推送到 ECR / GCR / 阿里云 ACR
  - Backend：ECS Fargate / Cloud Run / 阿里云 SAE
  - Frontend：S3+CloudFront / Vercel / 阿里云 OSS+CDN
  - Chroma：单独容器，云端用持久化卷
  - 提供 Helm chart 或 docker-compose.prod.yml

---

## 4. 前端设计

### 4.1 页面结构

```
/                    → 重定向到 /chat
/chat                → 主对话界面（问答/研究模式）
/chat/:sessionId     → 加载历史会话
/knowledge           → 知识库管理
/knowledge/:kbId     → 单个知识库详情
/settings            → 模型 / API Key 配置
```

### 4.2 侧边栏组件
三个模块卡片：
- **新对话**：按钮，点击清空当前 session 并跳到新对话
- **对话**：列出历史 session（标题、最后更新时间、模式标识），点击切换
- **知识库**：列出所有知识库（名称、文档数、向量数），点击进入管理

### 4.3 主对话界面

```
┌──────────────────────────────────────────────────┐
│ [问答模式 | 研究模式]    知识库: [选择 KB ▾]      │
├──────────────────────────────────────────────────┤
│                                                  │
│   ┌─ 消息气泡区域 ───────────────────────────┐  │
│   │  User: 研究一下 LLM 推理优化方向         │  │
│   │  Assistant: ...（研究模式显示进度步骤） │  │
│   └─────────────────────────────────────────┘  │
│                                                  │
│   ┌─ 输入框 ─────────────────────────────────┐  │
│   │  [输入区.............................]   │  │
│   │  [附件] [清空]              [发送 ➤]    │  │
│   └─────────────────────────────────────────┘  │
└──────────────────────────────────────────────────┘
```

**问答模式**：
- 单输入框，发送即得答案
- 可勾选 0..N 个知识库，挂载到 RAG
- 回答可带引用（出处、片段）

**研究模式**：
- 同样的输入框，但后端进入多 agent 工作流
- 前端用 SSE 接收进度，前端展示步骤卡：
  ```
  ✓ 规划研究范围
  ✓ 检索 arxiv（找到 42 篇）
  ⏳ 下载 PDF（12/42）
  ⏸ 解析与摘要
  ⏸ 生成综述
  ```
- 完成后渲染 markdown 综述，可下载 .md

### 4.4 知识库管理界面
- 创建 / 重命名 / 删除知识库
- 上传 PDF（走 `/knowledge/upload`，后端解析+分块+embedding 写入 Chroma）
- 列表展示已索引文档（标题、页数、向量化时间）
- 测试检索：输入 query，命中 top-k 片段预览

---

## 5. 后端设计

### 5.1 API 设计

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/sessions` | 创建会话（mode: qa \| research） |
| GET | `/api/sessions` | 列出所有会话 |
| GET | `/api/sessions/{id}` | 加载会话（含消息历史） |
| DELETE | `/api/sessions/{id}` | 删除会话 |
| POST | `/api/chat/qa` | 问答模式（非流式） |
| POST | `/api/chat/qa/stream` | 问答模式 SSE 流式（可选） |
| POST | `/api/research` | 研究模式 SSE 流式，返回每步进度 |
| GET/POST/DELETE | `/api/knowledge` `/api/knowledge/{id}` `/api/knowledge/{id}/docs` | 知识库 CRUD |
| POST | `/api/knowledge/{id}/search` | 知识库检索测试 |
| GET | `/metrics` | Prometheus |
| GET | `/health` | 健康检查 |

请求体约定：
```jsonc
// /api/chat/qa
{
  "session_id": "uuid",   // 可选，新建则不传
  "query": "用户问题",
  "kb_ids": ["kb1", "kb2"] // 可选，挂载知识库
}

// /api/research
{
  "session_id": "uuid",
  "query": "我想研究 LLM 推理优化",
  "max_papers": 30,        // 默认 20
  "depth": "normal"        // quick | normal | deep
}
```

SSE 事件类型：
```
event: step
data: {"node": "searcher", "status": "running", "message": "检索 arxiv..."}

event: step
data: {"node": "downloader", "status": "running", "progress": 0.4, "message": "12/30"}

event: token
data: {"delta": "...综述片段..."}     // 综述生成时的流式 token

event: final
data: {"report_markdown": "...", "papers": [...]}

event: error
data: {"message": "...", "trace_id": "..."}
```

### 5.2 状态机：研究模式多 Agent 流程

```
       ┌─────────┐
       │ Planner │   解析用户 query，拆成子问题、关键词、检索范围
       └────┬────┘
            │
       ┌────▼─────┐
       │ Searcher │   arxiv 检索 + 去重 + 排序（按相关度+引用数）
       └────┬─────┘
            │
       ┌────▼─────────┐
       │ Downloader   │   批量下载 PDF，断点续传，失败重试
       └────┬─────────┘
            │
       ┌────▼─────────┐
       │ Analyzer     │   pdfplumber 解析，逐篇提取：摘要、方法、结论
       └────┬─────────┘
            │
       ┌────▼─────────────┐
       │ Synthesizer      │   汇总分析，生成结构化综述
       └────┬─────────────┘
            │
       ┌────▼─────┐
       │  Reviewer │   自检：覆盖度、引用准确性；不通过则反馈给 Synthesizer 修订
       └────┬─────┘
            │
           done
```

LangGraph 实现（`StateGraph`）：
- **State**：`TypedDict`，包含 `query`, `sub_questions`, `papers`, `analyses`, `report_draft`, `trace_id`
- **节点**：每个 agent 是一个 `async def node(state) -> state` 函数
- **边**：默认线性，加一条 Reviewer → Synthesizer 的条件边（`if not ok: revise`）
- **持久化**：`MemorySaver`（默认） / `PostgresSaver`（生产，跨会话恢复）

### 5.3 各 Agent 职责

#### Planner
- 输入：用户原始 query
- 输出：`sub_questions: List[str]`, `keywords: List[str]`, `search_queries: List[str]`
- 实现：`ChatPromptTemplate` + LLM，结构化输出用 Pydantic schema

#### Searcher
- 工具：`arxiv.Search` 包装成 LangChain `Tool`
- 逻辑：每个 query 跑 arxiv，按相关度 + 年份过滤，合并去重
- 输出：`papers: List[PaperMeta]`（title / authors / year / abstract / arxiv_id / pdf_url）

#### Downloader
- 工具：自定义 Python 函数，从 arxiv PDF URL 下载到 `data/papers/{arxiv_id}.pdf`
- 策略：并发下载（asyncio + semaphore，限速 ≤5），失败重试 2 次
- 钩子：每下载一篇就 emit SSE 进度事件

#### Analyzer
- 工具：pdfplumber 抽文本，按段落分块（每篇 ≤ 2000 token 重叠 200）
- 每篇生成结构化摘要：
  ```json
  {
    "problem": "研究什么问题",
    "method": "核心方法",
    "results": "实验结果",
    "limitations": "局限性",
    "novelty": "相对已有工作的新意"
  }
  ```
- 输出：`analyses: List[PaperAnalysis]`

#### Synthesizer
- 输入：所有 analyses + 原始 sub_questions
- 提示词：要求结构化输出（引言 / 研究现状 / 方法分类 / 趋势 / 未来方向 / 参考文献）
- 引用：综述中每条结论都标注 `[arxiv_id]`
- 支持流式输出

#### Reviewer
- 自检 prompt：是否覆盖子问题、引用是否对得上、结构是否完整
- 不通过则返回 `feedback: str`，触发重写

### 5.4 问答模式

- 单 ReAct agent（`AgentExecutor` from LangChain，或自写 LangGraph 节点）
- Tools：
  - `kb_search(kb_id, query, k)`：查指定知识库（Chroma similarity）
  - `web_search(query)`（可选，后续接 Tavily/SerpAPI）
  - `arxiv_search(query)`（轻量检索问答时偶尔需要）
- 不挂载 KB 时就是纯 LLM 对话
- 挂载 KB 时 system prompt 注入检索结果作为 context

### 5.5 RAG 流水线

```
PDF 上传
  → pdfplumber 抽文本（按页）
  → 按段落 + 标题分块（RecursiveCharacterTextSplitter, chunk=800, overlap=150）
  → Embedding（默认 OpenAI text-embedding-3-small，可切 BGE）
  → 写入 Chroma collection（每 KB 一个 collection，metadata: {kb_id, doc_id, page}）
```

检索：query → embed → top-k=5 → MMR 重排 → 返回 `{text, metadata, score}`

---

## 6. 数据模型

### 6.1 Postgres / SQLite 表
```sql
sessions(
  id UUID PK, title TEXT, mode TEXT,  -- 'qa' | 'research'
  created_at, updated_at, user_id NULL  -- 单用户版 user_id 暂为 NULL
)

messages(
  id UUID PK, session_id UUID FK, role TEXT,  -- 'user' | 'assistant' | 'system'
  content TEXT, extra JSON,  -- extra: {kb_ids, tool_calls, trace_id, ...}
  created_at
)

knowledge_bases(
  id UUID PK, name TEXT, embedding_model TEXT,
  created_at, doc_count INT, vector_count INT
)

documents(
  id UUID PK, kb_id UUID FK, filename TEXT, page_count INT,
  indexed_at, status TEXT  -- 'pending' | 'indexed' | 'failed'
)
```

> ponytail: 第一版直接用 SQLite，单文件 `data/scholarai.db`，后期切 Postgres 改一行连接串。

### 6.2 文件系统布局
```
data/
  papers/                # 论文 PDF（按 arxiv_id 命名）
  chroma/                # Chroma 持久化目录
  uploads/               # 知识库上传的 PDF 临时区
  logs/                  # 结构化日志
  reports/               # 生成的综述（可选缓存）
```

---

## 7. 可观测性

| 维度 | 工具 | 输出 |
|------|------|------|
| LLM Trace | LangSmith / Langfuse | 每次调用的 prompt、completion、token、耗时 |
| 应用 Trace | OpenTelemetry + FastAPI 中间件 | 请求级 span，关联到 LLM span |
| 日志 | structlog + JSON handler | stdout 聚合，K8s 直接收 |
| 指标 | prometheus-client | `/metrics`：请求数、延迟直方图、各节点耗时、token 消耗 |
| 错误 | Sentry SDK（可选） | 异常聚合 + 上下文 |
| 调试 | LangGraph Studio / LangSmith Playground | 可视化逐步重放 |

**关键 trace_id 串联**：HTTP 请求生成 `X-Request-ID` → structlog context → 注入到 LangChain `RunnableConfig` 的 `metadata` → 出现在 LangSmith 中。后端响应头带回 `X-Request-ID` 给前端展示，方便排错时一键定位。

Prometheus 关键指标：
- `scholarai_requests_total{mode,endpoint}`
- `scholarai_request_duration_seconds{mode,endpoint}` (histogram)
- `scholarai_llm_tokens_total{model,type}` (type=input/output)
- `scholarai_node_duration_seconds{node}`
- `scholarai_kb_search_hits{kb_id}`

---

## 8. 部署方案

### 8.1 本地（docker-compose）
```yaml
# docker-compose.yml 关键服务
services:
  backend:
    build: ./backend
    env_file: .env
    ports: ["8000:8000"]
    volumes: ["./data:/app/data"]
    depends_on: [chroma]

  frontend:
    build: ./frontend
    ports: ["5173:80"]
    depends_on: [backend]

  chroma:
    image: chromadb/chroma:latest
    ports: ["8001:8000"]
    volumes: ["chroma_data:/chroma/chroma"]
```

启动：`docker compose up -d`，访问 `http://localhost:5173`。

### 8.2 云端
- **镜像**：后端用多阶段 Dockerfile（builder 装 poetry，生产镜像只拷 wheel）
- **Backend 部署**（任选）：
  - AWS ECS Fargate：task definition + ALB + EFS 挂载
  - GCP Cloud Run：直接容器，cold start 友好
  - 阿里云 SAE / 腾讯云轻量：国内最方便
- **Frontend 部署**：
  - `npm run build` → 静态资源
  - S3+CloudFront / Vercel / 阿里云 OSS+CDN
  - 环境变量 `VITE_API_BASE` 指向后端域名
- **存储**：
  - PDF：S3 / OSS，boto3 / oss2 替换本地 fs
  - 向量库：建议跑独立容器 + 云盘，或用 Pinecone（托管）替代 Chroma
- **密钥**：所有 API Key 走云厂商 Secret Manager / Parameter Store
- **HTTPS**：ALB / API Gateway 终结证书

> ponytail: 不写 K8s YAML 第一版。Fargate / Cloud Run 已经够用；要 K8s 时单独 `deploy/k8s/` 目录补 Helm chart。

### 8.3 配置管理
`.env`（不进 git）：
```
OPENAI_API_KEY=...
ANTHROPIC_API_KEY=...
LANGSMITH_API_KEY=...           # 可选
LANGSMITH_TRACING=true
LANGCHAIN_PROJECT=scholarai
CHROMA_HOST=chroma
DATABASE_URL=sqlite:///./data/scholarai.db   # 或 postgresql://...
PAPER_STORAGE_DIR=./data/papers
DEFAULT_LLM=gpt-4o-mini
EMBEDDING_MODEL=text-embedding-3-small
```

---

## 9. 项目结构

```
ScholarAI/
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   │   ├── Sidebar.tsx
│   │   │   ├── ChatPanel.tsx
│   │   │   ├── ResearchProgress.tsx
│   │   │   ├── KnowledgeBasePanel.tsx
│   │   │   └── ui/                # shadcn 组件
│   │   ├── hooks/
│   │   │   ├── useSSE.ts
│   │   │   └── useChat.ts
│   │   ├── pages/
│   │   │   ├── ChatPage.tsx
│   │   │   ├── KnowledgePage.tsx
│   │   │   └── SettingsPage.tsx
│   │   ├── lib/
│   │   │   └── api.ts
│   │   ├── store/                 # zustand
│   │   ├── App.tsx
│   │   └── main.tsx
│   ├── package.json
│   ├── vite.config.ts
│   ├── tailwind.config.js
│   └── Dockerfile
│
├── backend/
│   ├── app/
│   │   ├── main.py                # FastAPI 入口
│   │   ├── config.py              # pydantic-settings
│   │   ├── api/
│   │   │   ├── chat.py
│   │   │   ├── research.py
│   │   │   ├── knowledge.py
│   │   │   ├── sessions.py
│   │   │   └── health.py
│   │   ├── agents/
│   │   │   ├── qa_agent.py        # 问答单 agent
│   │   │   ├── research_graph.py  # LangGraph 装配
│   │   │   ├── nodes/
│   │   │   │   ├── planner.py
│   │   │   │   ├── searcher.py
│   │   │   │   ├── downloader.py
│   │   │   │   ├── analyzer.py
│   │   │   │   ├── synthesizer.py
│   │   │   │   └── reviewer.py
│   │   │   └── state.py           # State TypedDict
│   │   ├── tools/
│   │   │   ├── arxiv_tool.py
│   │   │   ├── pdf_parser.py
│   │   │   ├── kb_search.py
│   │   │   └── paper_storage.py
│   │   ├── rag/
│   │   │   ├── embeddings.py
│   │   │   ├── chunker.py
│   │   │   └── vector_store.py    # Chroma client
│   │   ├── db/
│   │   │   ├── models.py
│   │   │   ├── session.py         # SQLAlchemy
│   │   │   └── migrations/        # alembic
│   │   ├── observability/
│   │   │   ├── logging.py         # structlog
│   │   │   ├── tracing.py         # OTel + LangSmith
│   │   │   └── metrics.py         # prometheus
│   │   └── schemas/               # Pydantic
│   ├── tests/
│   │   ├── test_qa_agent.py
│   │   ├── test_research_graph.py
│   │   └── test_kb_api.py
│   ├── pyproject.toml
│   └── Dockerfile
│
├── deploy/
│   ├── docker-compose.yml         # 本地
│   ├── docker-compose.prod.yml    # 云端（带 nginx、env 文件）
│   ├── fargate/                   # AWS Fargate task 模板
│   └── README.md
│
├── .env.example
├── .gitignore
└── README.md
```

---

## 10. 关键依赖（pyproject.toml 摘要）

```toml
[project]
name = "scholarai-backend"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
  "fastapi>=0.115",
  "uvicorn[standard]>=0.32",
  "langchain>=0.3",
  "langgraph>=0.2",
  "langchain-openai>=0.2",       # 默认 OpenAI
  "langchain-anthropic>=0.2",    # 可选
  "chromadb>=0.5",
  "pdfplumber>=0.11",
  "pymupdf>=1.24",               # 兜底
  "arxiv>=2.1",
  "sqlalchemy>=2.0",
  "alembic>=1.13",
  "pydantic>=2.8",
  "pydantic-settings>=2.5",
  "structlog>=24.4",
  "prometheus-client>=0.21",
  "opentelemetry-instrumentation-fastapi>=0.48b0",
  "tenacity>=9.0",               # 重试
  "httpx>=0.27",
]
```

---

## 11. 实施路线（建议 6 个里程碑）

| 阶段 | 目标 | 验收 |
|------|------|------|
| M1 · 骨架 | FastAPI + React 脚手架，能跑通"问答模式走 OpenAI" | 前端输入 → 后端返回 LLM 回答 |
| M2 · 问答+知识库 | 知识库 CRUD + Chroma 写入 + 问答 RAG 增强 | 上传 PDF，引用 KB 答出带出处的答案 |
| M3 · 研究模式 v1 | Planner / Searcher / Synthesizer 跑通，先不下 PDF | 给出研究综述（基于 abstract） |
| M4 · 研究模式 v2 | 加 Downloader + Analyzer，引入 pdfplumber | 完整论文级综述，含方法/实验抽取 |
| M5 · 可观测性 | LangSmith 接入、Prometheus 指标、结构化日志 | 一次研究请求能完整 trace 回放 |
| M6 · 部署 | docker-compose 一键起，文档化云端部署步骤 | 新机器 5 分钟内拉起全栈 |

---

## 12. 风险与权衡

| 风险 | 缓解 |
|------|------|
| arxiv API 限流 / 失败 | tenacious 重试 + 缓存检索结果到 SQLite |
| 大综述 LLM 上下文超限 | 摘要阶段先 Map-Reduce 压缩每篇，再汇总 |
| Chroma 内存占用 | 启用持久化，chroma 单独容器并限制资源 |
| PDF 解析失败（扫描件） | 接入 PyMuPDF；未来加 OCR（paddleocr） |
| 引用准确性 | Reviewer 节点强校验 `paper_id` 存在性 |
| LLM 成本失控 | 限速 + 缓存相同 query；深度档位控制 max_papers |

---

## 13. 待确认的设计选择（默认已给定，可改）

1. **LLM 默认**：OpenAI `gpt-4o-mini`（成本/质量平衡），可切 `claude-sonnet-5` / `qwen-plus` / 本地 Ollama
2. **Embedding**：`text-embedding-3-small`（默认），可切 BGE / m3e
3. **云端目标**：方案不锁死，给 Fargate / Cloud Run / SAE 三套任选
4. **多用户**：第一版单用户（无 auth），预留 `user_id` 字段；接 SSO 留到 v0.2
5. **论文存储**：本地 FS → S3/OSS，抽象成 `PaperStorage` 接口

---

## 14. 立即可做（下一步）

1. 起后端骨架：`poetry init` + 装依赖 + `app/main.py` 写 hello world
2. 起前端骨架：`npm create vite@latest` + Tailwind + shadcn init
3. 跑通"前端 → 后端 → OpenAI"三跳
4. 接入 Chroma，实现最简 RAG
5. 接入 LangGraph，做 3 节点 demo（Planner / Searcher / Synthesizer）

确认这份方案后，我可以直接开 M1。
