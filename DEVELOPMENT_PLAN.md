# ScholarAI · 分阶段开发计划

> 版本：v0.1 ｜ 2026-07-16
> 上游：[IMPLEMENTATION_PLAN.md](./IMPLEMENTATION_PLAN.md)
> 默认 LLM：DeepSeek（dev 用 `deepseek-v4-flash`，生产可切 OpenAI / Anthropic / Qwen）
> 原则：每个子阶段（标 a/b/c）完成 + 测试通过才进入下一个；不通过就修，不要跳。

---

## 0. 总体约定

### 0.1 仓库与目录
单仓 monorepo：
```
/home/gh/ScholarAI/
├── frontend/          # React + Vite + TS
├── backend/           # FastAPI + LangGraph
├── deploy/            # docker-compose / k8s
├── apikey.txt         # 本地秘密（gitignored，不提交）
├── .env.example       # 环境变量模板（提交）
├── .gitignore
├── IMPLEMENTATION_PLAN.md
└── DEVELOPMENT_PLAN.md
```

### 0.2 Git 策略
- 主分支：`main`（永远可跑）
- 每完成一个子阶段：`git commit -m "M{x}.{y}: <一句话描述>"`
- 阶段末打 tag：`v0.1.0-m1` / `v0.1.0-m2` ...
- 不强制 PR，单人开发直接 commit 到 main 即可

### 0.3 提交粒度（commit = 1 个可验证的变更）
- ✅ "M1.2: backend skeleton with health endpoint"
- ❌ "一大波更新"

### 0.4 代码风格
- **后端**：Ruff（lint+format）+ mypy（strict 关掉，核心 schema 严格）
- **前端**：ESLint（airbnb-typescript）+ Prettier + Tailwind class sort
- 行宽 100，Python 用 4 空格，TS 用 2 空格
- 不写 docstring 除非函数非显然；公开 API 函数写一行 docstring

### 0.5 测试策略
- **后端**：pytest + httpx.AsyncClient（FastAPI 异步测试）
  - 每个子阶段一个 `tests/test_m{x}_{name}.py`，至少 1 个 happy path + 1 个失败 path
  - LLM 调用一律 mock（避免跑测试烧钱 + 依赖网络）
  - 真实 LLM 联调用 `tests/integration/` 标记 `@pytest.mark.integration`，平时跳过
- **前端**：vitest + @testing-library/react
  - 每个组件至少 1 个 render 测试
  - 关键 hook（useSSE）独立测试
- **手动 smoke**：每阶段末尾在 README 或本文件写 3-5 行 curl / 点击步骤

> ponytail: 不写覆盖率门槛（YAGNI）。够用即可。

### 0.6 环境变量
`.env.example`（提交）：
```bash
# LLM
LLM_PROVIDER=deepseek              # deepseek | openai | anthropic | ollama
LLM_API_KEY=                       # 留空，从 apikey.txt 复制
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-v4-flash
LLM_TIMEOUT_SECONDS=60             # 单次 LLM 调用的硬超时

# Embedding（dev 用本地 BGE；生产可换 openai / qwen）
EMBEDDING_PROVIDER=bge             # bge | openai | qwen
BGE_MODEL=BAAI/bge-small-zh-v1.5
BGE_DEVICE=cpu                     # cpu | cuda | mps
# EMBEDDING_PROVIDER=openai 时启用：
# OPENAI_API_KEY=

# Storage
DATABASE_URL=sqlite:///./data/scholarai.db
PAPER_STORAGE_DIR=./data/papers
REPORTS_DIR=./data/reports         # 研究综述落盘目录
CHROMA_PERSIST_DIR=./data/chroma
UPLOAD_DIR=./data/uploads
UPLOAD_MAX_SIZE_MB=50
UPLOAD_MAX_PAGES=500

# Observability
LANGSMITH_TRACING=false
LANGSMITH_API_KEY=
LANGSMITH_PROJECT=scholarai
LANGCHAIN_SAMPLE_RATE=1.0          # 生产建议 0.1 (10%)
LOG_LEVEL=INFO

# App
CORS_ORIGINS=http://localhost:5173
API_PORT=8000
RESEARCH_TIMEOUT_SECONDS=300       # 单次研究请求的总超时
```

`.gitignore` 必须包含：`apikey.txt`、`.env`、`data/`、`node_modules/`、`__pycache__/`、`*.pyc`、`dist/`、`.venv/`。

> apikey.txt 是用户已经准备好的本地文件，**不要复制内容到任何 git 跟踪的文件里**。`backend/.env` 从它复制（或用 `make init-env` 脚本读它生成）。

### 0.7 Makefile 速查
仓库根放一个 `Makefile`，每个阶段用一个 target：
```makefile
.PHONY: init-env up-be up-fe test-be test-fe m1 m2 ...

init-env:
	@if [ ! -f backend/.env ]; then cp .env.example backend/.env && sed -i 's/^LLM_API_KEY=.*/LLM_API_KEY=$$(awk -F: '\''/^apikey:/{print $$2}'\'' apikey.txt)/' backend/.env; fi

up-be:
	cd backend && uvicorn app.main:app --reload --port 8000

up-fe:
	cd frontend && npm run dev

test-be:
	cd backend && pytest -v

test-fe:
	cd frontend && npm test
```

---

## 1. 阶段总览

| 阶段 | 主题 | 预计子阶段数 | 退出标志 |
|------|------|--------------|----------|
| **M1** | 骨架：前后端跑通单轮问答 | 4 | 前端输入 → DeepSeek 回话 |
| **M2** | 知识库 + Chroma RAG | 5 | 上传 PDF → 问答带出处 |
| **M2.6** | 对话记录持久化 + 侧边栏 | 8 | 重启不丢 + 侧边栏能看历史 |
| **M3** | 研究模式 v1：3 节点（abstract 综述） | 4 | 给方向 → 拿到 abstract 级综述 |
| **M4** | 研究模式 v2：下载 + PDF 解析 | 4 | 给方向 → 拿到论文级综述 |
| **M5** | 可观测性 | 4 | LangSmith 看得到 trace，`/metrics` 暴露 |
| **M6** | 部署 | 4 | `docker compose up` 拉起完整栈 |

每完成一个阶段打 tag，回滚方便。

---

## 2. M1 · 骨架

### M1.0 前置（5 分钟）
```bash
cd /home/gh/ScholarAI
git init
# 创建 .gitignore、.env.example、Makefile、README
git add . && git commit -m "chore: initial repo scaffolding"
```

### M1.1 后端最小骨架
**目标**：`/health` 返回 200，配置可加载。

**文件**：
- `backend/pyproject.toml`（用 poetry 或 uv，建议 uv：更快）
- `backend/app/__init__.py`
- `backend/app/config.py`：`Settings(BaseSettings)`，从 `backend/.env` 读
- `backend/app/main.py`：FastAPI 实例 + `/health`
- `backend/tests/test_health.py`

**关键代码骨架**：
```python
# app/config.py
from pydantic_settings import BaseSettings, SettingsConfigDict
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    llm_provider: str
    llm_api_key: str
    llm_base_url: str
    llm_model: str
    api_port: int = 8000
    cors_origins: list[str] = ["http://localhost:5173"]
settings = Settings()
```

```python
# app/main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
app = FastAPI(title="ScholarAI")
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"])
@app.get("/health")
def health(): return {"ok": True}
```

**验证**：
```bash
cd backend && uv venv && source .venv/bin/activate
uv pip install -e .   # 或 poetry install
uvicorn app.main:app --reload --port 8000
curl http://localhost:8000/health   # {"ok":true}
pytest -v
```
✅ → commit `M1.1: backend skeleton with config and health`

### M1.2 前端最小骨架
**目标**：Vite + React + TS + Tailwind 起项目，渲染"Hello ScholarAI"。

**步骤**：
```bash
cd /home/gh/ScholarAI
npm create vite@latest frontend -- --template react-ts
cd frontend
npm i
npm i -D tailwindcss@3 postcss autoprefixer
npx tailwindcss init -p
npm i react-router-dom zustand
```

**配置**：
- `tailwind.config.js` 的 `content` 加 `"./index.html","./src/**/*.{ts,tsx}"`
- `src/index.css` 加 `@tailwind base; @tailwind components; @tailwind utilities;`
- `src/App.tsx` 写最小页面，react-router 配 `/` → ChatPage 占位
- `vite.config.ts` 加 proxy：
  ```ts
  server: { proxy: { '/api': { target: 'http://localhost:8000', changeOrigin: true } } }
  ```

**验证**：
```bash
npm run dev   # 打开 http://localhost:5173 看到 Hello
```
✅ → commit `M1.2: frontend skeleton with vite tailwind router`

### M1.3 对接：单轮问答（mock LLM）
**目标**：前端输入 → 后端 → 返回 mock 字符串。

**后端**：
- `app/api/chat.py`：`POST /api/chat/qa` 接受 `{query: str}`，先硬返回 `f"你问的是：{query}"`
- `app/main.py`：`app.include_router(chat.router, prefix="/api")`
- 测试：`tests/test_chat.py` 覆盖 happy + 字段缺失

**前端**：
- `src/lib/api.ts`：薄封装 `fetch("/api/chat/qa", {method:"POST", body:JSON.stringify(...)})`
- `src/components/ChatPanel.tsx`：输入框 + 发送按钮 + 消息列表（useState）
- `src/pages/ChatPage.tsx`：放 ChatPanel

**验证**：
```bash
# 后端跑着，前端跑着
# 浏览器开 localhost:5173，输入"你好" → 显示"你问的是：你好"
# 也可直接 curl：
curl -X POST http://localhost:8000/api/chat/qa -H "Content-Type: application/json" -d '{"query":"hi"}'
```
✅ → commit `M1.3: mock chat endpoint wired to frontend`

### M1.4 接入真实 LLM（DeepSeek）
**目标**：问答走 DeepSeek。

**改动**：
- `backend/app/llm.py`：
  ```python
  from langchain_openai import ChatOpenAI  # DeepSeek 兼容 OpenAI 接口
  from app.config import settings
  def get_llm():
      return ChatOpenAI(
          model=settings.llm_model,
          api_key=settings.llm_api_key,
          base_url=settings.llm_base_url,
          temperature=0.3,
      )
  ```
- `app/api/chat.py`：用 `get_llm().invoke(query)` 返回 `result.content`
- 测试用 `unittest.mock` patch `get_llm`，避免烧 token

**验证**：
- pytest 全过（mock 模式）
- 手动：前端输入"用一句话介绍 LangGraph" → 看到 DeepSeek 真实回答
- 终端日志确认调用 `deepseek-v4-flash`

✅ → commit `M1.4: deepseek LLM integration` → **打 tag v0.1.0-m1**

**M1 退出检查**：
- [ ] `/health` 通
- [ ] 前端能发问题、收回答
- [ ] `pytest backend` 全过
- [ ] `npm test frontend` 全过
- [ ] `.env`、`apikey.txt` 不在 git 跟踪里

---

## 3. M2 · 知识库 + RAG

### M2.1 数据库与模型
**目标**：会话、消息、KB、文档四张表建好，SQLAlchemy 2.0 异步。

**文件**：
- `backend/app/db/__init__.py`
- `backend/app/db/session.py`：`async_engine`, `AsyncSessionLocal`, `get_db` 依赖
- `backend/app/db/models.py`：`Session`, `Message`, `KnowledgeBase`, `Document` 四个 ORM
- `backend/alembic.ini` + `backend/app/db/migrations/`：alembic 初始化
- `backend/tests/test_models.py`：CRUD smoke

**关键点**：
- `Session` 字段：`id (UUID)`, `title`, `mode ('qa'|'research')`, `created_at`, `updated_at`
- `Message`：`id`, `session_id (FK)`, `role`, `content`, `extra (JSON)`, `created_at`
- `KnowledgeBase`：`id`, `name`, `embedding_model`, `created_at`, `doc_count`, `vector_count`
- `Document`：`id`, `kb_id (FK)`, `filename`, `page_count`, `status`, `indexed_at`
- UUID 用 `uuid7`（时间有序，索引友好）
- 软删除：加 `deleted_at` 字段，第一版可省

**验证**：
```bash
alembic revision --autogenerate -m "init schema"
alembic upgrade head
pytest tests/test_models.py
sqlite3 data/scholarai.db ".tables"   # 看到 4 张表
```

### M2.2 Chroma 客户端封装
**目标**：`VectorStore` 单例，按 kb_id 隔离 collection。

**文件**：
- `backend/app/rag/__init__.py`
- `backend/app/rag/embeddings.py`：
  ```python
  from functools import lru_cache
  from langchain_community.embeddings import HuggingFaceBgeEmbeddings
  from app.config import settings

  @lru_cache(maxsize=1)
  def get_embeddings():
      if settings.embedding_provider == "bge":
          return HuggingFaceBgeEmbeddings(
              model_name=settings.bge_model,
              model_kwargs={"device": settings.bge_device},
              encode_kwargs={"normalize_embeddings": True},  # BGE 官方建议
          )
      # 后续按需扩展 openai / qwen
      raise NotImplementedError(f"embedding provider {settings.embedding_provider} not implemented yet")
  ```
  - 依赖：`sentence-transformers` + `torch`（CPU 版即可，~250MB 首次下载模型）
  - `BAAI/bge-small-zh-v1.5` 模型权重首次跑时自动下载到 `~/.cache/huggingface/`
  - 单元测试用 1 句话断言 embed 向量维度 = 512 且非零
- `backend/app/rag/vector_store.py`：
  ```python
  import chromadb
  from chromadb.config import Settings as ChromaSettings
  _client = None
  def get_chroma():
      global _client
      if _client is None:
          _client = chromadb.PersistentClient(path=settings.chroma_persist_dir)
      return _client
  def get_or_create_collection(kb_id: str):
      return get_chroma().get_or_create_collection(name=kb_id, metadata={"hnsw:space":"cosine"})
  ```
- `tests/test_vector_store.py`：创建 collection、add、query

**验证**：
```bash
pytest tests/test_vector_store.py
# 手动 python -c 验证 add/query 返回 top-k
```

### M2.3 文档处理流水线
**目标**：PDF → 分块 → embedding → 写入 Chroma。

**文件**：
- `backend/app/rag/chunker.py`：`split_documents(text, chunk_size=800, overlap=150)` 用 `langchain.text_splitter.RecursiveCharacterTextSplitter`
- `backend/app/rag/pdf_loader.py`：用 `pdfplumber.open(path)` 抽每页文本，返回 `[{page: int, text: str}]`
- `backend/app/rag/indexer.py`：
  ```python
  def index_pdf(kb_id: str, pdf_path: str, doc_id: str) -> int:
      pages = load_pdf(pdf_path)               # pdfplumber
      chunks = split_documents(pages)          # langchain
      embeddings = get_embeddings().embed_documents([c.text for c in chunks])
      coll = get_or_create_collection(kb_id)
      coll.add(ids=[f"{doc_id}_{i}" for i in range(len(chunks))],
               embeddings=embeddings,
               documents=[c.text for c in chunks],
               metadatas=[{"page":c.page, "doc_id":doc_id} for c in chunks])
      return len(chunks)
  ```
- `tests/test_indexer.py`：用 1 页小 PDF 走完流程，断言 Chroma 中 vector 计数 +1

**验证**：
```bash
pytest tests/test_indexer.py
# 手动：放一个 PDF 跑 indexer，chroma/ 目录有新文件
```

### M2.4 KB CRUD API
**目标**：`/api/knowledge` 完整 CRUD + 上传。

**端点**：
```
POST   /api/knowledge              # {name} → 创建 KB
GET    /api/knowledge              # 列表
GET    /api/knowledge/{id}         # 详情
DELETE /api/knowledge/{id}         # 删 KB（含 chroma collection + documents）
POST   /api/knowledge/{id}/docs    # multipart 上传 PDF → 后台 index
GET    /api/knowledge/{id}/docs    # 文档列表
POST   /api/knowledge/{id}/search  # {query, k=5} → 返回命中片段
```

**文件**：
- `backend/app/api/knowledge.py`
- `backend/app/schemas/knowledge.py`：Pydantic schemas
- 测试覆盖：创建/列表/上传/搜索/删除；上传非 PDF 应 400

**实现要点**：
- 上传走 `UploadFile`，先存到 `UPLOAD_DIR`，后调 `indexer.index_pdf` 写 Chroma，更新 Document.status
- 失败要回滚（删文件 + 标 status='failed'）
- 删除 KB：先 `chroma.delete_collection(name=kb_id)`，再删 documents + kb 行

**验证**：
```bash
# 准备一个小 PDF（论文首页或一段 LaTeX 编译出的 PDF）
curl -X POST http://localhost:8000/api/knowledge -H 'Content-Type: application/json' -d '{"name":"测试 KB"}'
# 拿到 kb_id
curl -X POST http://localhost:8000/api/knowledge/<kb_id>/docs -F 'file=@test.pdf'
curl -X POST http://localhost:8000/api/knowledge/<kb_id>/search -H 'Content-Type: application/json' -d '{"query":"这段话的关键概念", "k":3}'
```

### M2.5 问答挂载 KB（RAG）
**目标**：`/api/chat/qa` 支持 `kb_ids` 字段，命中知识库时带出处。

**改动**：
- `app/api/chat.py` 请求 schema 改为 `{query, kb_ids?: list[str], session_id?: str}`
- 实现：若 `kb_ids` 非空 → 各 KB 各取 top-5 → 合并去重 → 拼成 context 注入 system prompt → 调 LLM
- 响应 schema 增加 `citations: list[{kb_id, doc_id, page, text, score}]`
- prompt 模板：
  ```
  你是一个学术助手。请基于以下参考资料回答用户问题。
  回答中引用时用 [来源编号] 标注，编号对应下面的"参考资料"。
  如果资料不包含答案，明确说"知识库中未找到"。

  参考资料：
  [1] (kb:test, doc:abc, p:3) ...摘要片段...
  [2] (kb:test, doc:def, p:1) ...

  用户问题：{query}
  ```
- 测试：mock embeddings + mock LLM，断言 citations 不为空且 prompt 中包含检索文本

**前端改动**：
- `ChatPanel.tsx` 加 KB 多选下拉（从 `/api/knowledge` 拉列表）
- 回答区显示引用气泡（hover/点击展开详情）

**验证**：
- 上传一篇 LLM 综述 PDF → 创建 KB → 问"RAG 的核心挑战是什么" → 答案含 `[1]` 引用 → 点击引用看到对应页码 + 片段
- 没传 KB 时退回纯 LLM 回答

✅ → commit 链打完 → **打 tag v0.1.0-m2**

**M2 退出检查**：
- [ ] KB 创建 / 上传 / 搜索 / 删除全通
- [ ] 问答引用标注正确
- [ ] 4 个表都有，alembic 迁移可重放
- [ ] Chroma 数据持久化（重启不丢）

---

## 3.5 M2.6 · 对话记录持久化 + 侧边栏会话列表

> **背景**：M1.4 用 `InMemorySessionStore` 临时存 session，进程重启数据丢失。侧边栏「对话」模块一直空着。本节把会话从内存迁到 DB，并补完整 UI。

### M2.6.1 把 session_store 切到 DB
- `backend/app/session_store.py` 改用 `SessionModel` + `MessageModel`（已存在）
- 保留 `get_or_create / recent / add_message` 同样接口 → 其它代码（chat.py）零改动
- 启动时从 DB 加载已有 session 到内存缓存（避免每次查 DB）
- 写消息时同步刷到 DB（写穿透）

**关键**：用 SQLAlchemy 同步 Session 即可（与 M2.1 决策一致），FastAPI sync handler 在线程池跑

### M2.6.2 Session 标题自动生成
- 新 session 第一条消息进来时：截前 20 字作 title（**不用 LLM，省 token**）
- 用户可手动重命名（M2.6.5）

### M2.6.3 Sessions API
```
GET    /api/sessions              # 列表（含 message_count 预览）
GET    /api/sessions/{id}         # 详情 + 消息历史
DELETE /api/sessions/{id}         # 删 session（级联删 messages）
PATCH  /api/sessions/{id}         # 改 title
```
文件：`backend/app/api/sessions.py` + `backend/app/schemas/session.py`

### M2.6.4 前端 Session 列表 API
- `src/lib/api.ts` 加 `listSessions / getSession / deleteSession / updateSession`
- 测试 4 个 happy path

### M2.6.5 前端 Sidebar 对话列表
- 把 `Sidebar.tsx` 的「对话」NavLink 改成可展开列表
- 用 `useEffect` 拉 `/api/sessions`，渲染每条：title + 相对时间
- 每条 hover 显示删除按钮（trash icon）
- 当前 session 高亮
- 「+ 新对话」按钮：导航到 `/chat`（不带 session_id 触发新 session）

### M2.6.6 前端 ChatPanel 加载历史
- `useParams<{ sessionId?: string }>()` 拿路由里的 sessionId
- 第一次 mount：如果有 sessionId → 调 `getSession` 拉历史消息塞进 state
- 切到新 session：清空 state 后再拉
- 没 sessionId：保持原行为（新对话）

### M2.6.7 路由 + 整合
- `App.tsx`：`<Route path="/chat" />` 和 `<Route path="/chat/:sessionId" />` 都用 ChatPage
- ChatPage 把 sessionId 透传给 ChatPanel

### M2.6.8 退出检查
- [x] session 写入 DB，重启后端不丢
- [x] Sidebar「对话」显示列表，能点开旧 session
- [x] 能删除 session
- [x] /chat/:id 路由能加载历史消息
- [x] 标题自动生成（无 LLM 调用）

---

## 4. M3 · 研究模式 v1（abstract 级综述）

### M3.1 LangGraph 状态定义
**文件**：`backend/app/agents/state.py`
```python
from typing import TypedDict, Annotated
from operator import add
class ResearchState(TypedDict):
    query: str
    sub_questions: list[str]
    search_queries: list[str]
    papers: list[dict]        # arxiv 检索结果
    analyses: list[dict]      # M4 才填，先空
    report_draft: str
    feedback: str
    iteration: int
```
注：暂时不引入 `add` reducer（M4 再加），所有字段直接覆盖。

### M3.2 Planner / Searcher / Synthesizer 三个节点
**文件**：
- `backend/app/agents/nodes/planner.py`
- `backend/app/agents/nodes/searcher.py`（用 arxiv SDK 调 arxiv API）
- `backend/app/agents/nodes/synthesizer.py`（直接基于 abstract 写综述）
- `backend/app/agents/nodes/__init__.py`（空）

**Planner**：
- prompt：让 LLM 把 query 拆成 3-5 个子问题、生成 3-5 个 arxiv 检索词
- 用 `langchain.output_parsers.PydanticOutputParser` + pydantic schema 强约束输出
- 返回更新 state：`{sub_questions, search_queries}`

**Searcher**：
- 用 `arxiv.Search(query=q, max_results=10, sort_by=arxiv.SortCriterion.Relevance)` 跑每个 query
- 合并去重（按 arxiv_id），按年份过滤（默认 2018+）
- 返回更新 state：`{papers: [{arxiv_id, title, authors, year, abstract, pdf_url}]}`
- 加 3 秒 sleep 礼貌限速

**Synthesizer**：
- prompt：把 papers 的 abstract 喂给 LLM，要求输出结构化综述（引言 / 现状 / 方法分类 / 趋势 / 未来方向）
- 引用用 `[arxiv_id]`
- 不接 Reviewer（M3.4 才接）
- 返回更新 state：`{report_draft}`

### M3.3 LangGraph 装配 + 后端 API
**文件**：
- `backend/app/agents/research_graph.py`：
  ```python
  from langgraph.graph import StateGraph, START, END
  from app.agents.state import ResearchState
  from app.agents.nodes import planner, searcher, synthesizer

  def build_graph():
      g = StateGraph(ResearchState)
      g.add_node("planner", planner.run)
      g.add_node("searcher", searcher.run)
      g.add_node("synthesizer", synthesizer.run)
      g.add_edge(START, "planner")
      g.add_edge("planner", "searcher")
      g.add_edge("searcher", "synthesizer")
      g.add_edge("synthesizer", END)
      return g.compile()
  ```
- `backend/app/api/research.py`：
  - `POST /api/research` 接收 `{query, max_papers=20, depth="normal", session_id?}`
  - 第一版**非流式**：用 `graph.invoke(state)` 一次性返回 `{report_markdown, papers}`
  - 创建/更新 Session(mode='research') 和 Message 记录
  - **综述落盘**：写 `data/reports/{session_id}.md`（`REPORTS_DIR` 配置，默认 `./data/reports`），响应带 `report_path`；下载走静态返回该文件
  - **depth 档位**（映射到检索规模，不引入新节点）：
    - `quick`：max_papers≤8，searcher 每 query top-5，1 轮
    - `normal`：max_papers≤20，top-10
    - `deep`：max_papers≤40，top-15
    - depth 只调 searcher 的 `max_results` 与 max_papers 上限，Synthesizer prompt 不变

**测试**：
- `tests/test_research_graph.py`：
  - mock Planner LLM 输出 → 检查 state.sub_questions
  - mock Searcher 返回固定 papers → 检查 state.papers 数量
  - mock Synthesizer → 检查 report_draft
  - 端到端：mock 全部 LLM，POST `/api/research` 返回 200 + report

### M3.4 SSE 流式 + 前端进度
**后端**：
- `backend/app/api/research.py` 新增 `POST /api/research/stream` 返回 `StreamingResponse(media_type="text/event-stream")`
- 用 LangGraph 的 `graph.stream(state, stream_mode="updates")`：
  ```python
  async def event_generator():
      async for chunk in graph.astream(initial_state, stream_mode="updates"):
          node_name = list(chunk.keys())[0]
          yield f"event: step\ndata: {json.dumps({'node': node_name, 'status':'done'})}\n\n"
  ```
- 终态额外 `event: final\ndata: {...完整 report...}`

**前端**（注意：浏览器原生 `EventSource` 只支持 GET，研究接口要 POST + body，必须自己用 fetch + ReadableStream 解析）：
- `src/hooks/useSSE.ts`：
  ```ts
  export function useSSE<T = unknown>(url: string, body: unknown, onEvent: (event: string, data: T) => void) {
    const ctrl = new AbortController();
    const connect = async () => {
      const res = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json", "Accept": "text/event-stream" },
        body: JSON.stringify(body),
        signal: ctrl.signal,
      });
      if (!res.ok || !res.body) throw new Error(`SSE connect failed: ${res.status}`);
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        // SSE 消息以 \n\n 分隔
        let idx;
        while ((idx = buf.indexOf("\n\n")) !== -1) {
          const raw = buf.slice(0, idx); buf = buf.slice(idx + 2);
          const lines = raw.split("\n");
          let event = "message", data = "";
          for (const line of lines) {
            if (line.startsWith("event:")) event = line.slice(6).trim();
            else if (line.startsWith("data:")) data += line.slice(5).trim();
          }
          if (data) onEvent(event, JSON.parse(data));
        }
      }
    };
    connect().catch(err => onEvent("error", { message: String(err) }));
    return () => ctrl.abort();   // 组件卸载时取消
  }
  ```
- 服务端每 15s 发一次心跳注释行 `: ping\n\n`（不触发前端 onEvent，纯粹保活）
- `src/components/ResearchProgress.tsx`：步骤列表，每收到 `step` 事件点亮对应节点
- `ChatPage` 加模式切换：`mode === 'research'` 时显示 ResearchProgress + 走流式端点
- 报告完成后用 `react-markdown` 渲染；下载 .md 直接指向后端 `report_path`（§M3.3 已落盘），不在前端拼字符串
- **侧边栏会话 mode 徽章**：`Sidebar.tsx` 每条会话按 `s.mode` 显示 `问答`/`研究` 小标签（M2.6.5 数据已就绪，研究模式落地后补渲染）

> ponytail: **不做 token 级流式**（`event: token`）。综述最后随 `event: final` 一次性返回。
> 打字机效果 UX 更好但要在 synthesizer 节点内透传 LLM token 流，复杂度高，留 v0.2。

**验证**：
- 端到端：前端输入"研究 LLM 推理优化" → 看到 3 步进度依次亮起 → 收到综述 markdown
- 服务端日志能看到每个 node 的开始 / 结束 + 耗时

✅ → **打 tag v0.1.0-m3**

**M3 退出检查**：
- [ ] `/api/research` 同步端点通
- [ ] `/api/research/stream` SSE 通，前端能看到进度
- [ ] 综述含 `[arxiv_id]` 引用
- [ ] 论文数据写入 Session.messages（可查历史）

---

## 5. M4 · 研究模式 v2（PDF 解析）

### M4.1 Downloader 节点
**文件**：`backend/app/agents/nodes/downloader.py`
- 用 `httpx.AsyncClient` 异步下载 `papers[i].pdf_url` 到 `PAPER_STORAGE_DIR/{arxiv_id}.pdf`
- `asyncio.Semaphore(5)` 限速
- 失败重试 2 次（tenacity）
- 下载完更新 papers[i].local_path
- 返回：`{papers: [更新后的列表], download_failures: [arxiv_id]}`
- 失败论文不阻断流程：analyser 跳过，synthesizer 提示"X 篇下载失败"

**测试**：
- mock httpx 成功 / 失败 / 5xx → 检查 state.papers 状态
- e2e：下载 1 篇真实小 arxiv PDF（如 2401.00001）→ 文件存在且非 0 字节

### M4.2 Analyzer 节点
**文件**：`backend/app/agents/nodes/analyzer.py`
- 对每篇 `local_path` 存在的论文：
  - pdfplumber 抽文本
  - 截取前 N token（避免超 LLM 上下文，N=8000）
  - LLM 提取结构化字段：`problem, method, results, limitations, novelty`
  - 用 PydanticOutputParser 强约束
- 返回：`{analyses: [{arxiv_id, problem, method, ...}]}`
- 并发：用 `asyncio.gather` 跑所有论文（限 5 并发）

**测试**：
- 准备 fixture PDF（几页文本）
- mock LLM → 检查 analyses 字段完整性

### M4.3 升级 Synthesizer + 加 Reviewer
**Synthesizer 升级**：
- 输入改为 `papers + analyses`（M3 只看 abstract）
- prompt 中说明"以下每篇有结构化分析，请基于此综述"
- 综述章节里为每篇的核心贡献写一段

**Reviewer 节点**（新增）：
- `backend/app/agents/nodes/reviewer.py`
- 输入 `report_draft + papers + sub_questions`
- LLM 自检：
  - `covered_subquestions: list[str]`（必须覆盖所有 sub_questions）
  - `citation_accuracy: 'ok' | 'errors'`（检查每个 `[arxiv_id]` 是否在 papers 中存在）
  - `issues: list[str]`
  - `pass: bool`
- LangGraph 加条件边：`reviewer → (pass ? END : synthesizer)`，最多 2 轮重写

**装配更新**：
```python
g.add_node("reviewer", reviewer.run)
g.add_edge("synthesizer", "reviewer")
g.add_conditional_edges("reviewer", lambda s: END if s.get("pass") or s.get("iteration",0)>=2 else "synthesizer",
                        {END: END, "synthesizer": "synthesizer"})
# synthesizer 节点收到 feedback 时把 feedback 拼进 prompt
```

### M4.4 端到端验证
- 选一个有 10+ 引用的小众方向（如 "neural network pruning 2024"）
- 跑完整流程：planner → searcher → downloader → analyzer → synthesizer → reviewer
- 检查：综述有结构化分析引用，PDF 实际下载到 `data/papers/`，下载失败有标注
- 性能：20 篇论文全流程 ≤ 5 分钟

✅ → **打 tag v0.1.0-m4**

**M4 退出检查**：
- [ ] `data/papers/` 有真实 PDF 文件
- [ ] 每篇 analyses 字段完整（5 个字段都非空）
- [ ] 综述引用对得上 papers
- [ ] 失败论文不阻断流程

---

## 6. M5 · 可观测性

### M5.1 structlog 结构化日志
**文件**：`backend/app/observability/logging.py`
- JSON 格式输出到 stdout
- 绑定 `request_id` / `session_id` / `node_name` 上下文
- FastAPI middleware：从 header `X-Request-ID` 读，没有就生成
- 每个 agent 节点入口 `log.info("node.start", node=name)`，出口 `log.info("node.end", duration=...)`

**验证**：
- 一次 `/api/research` 请求 → 日志是 JSON 行，含 request_id 串起所有 node
- `jq '.msg'` 能看到 `node.start` / `node.end` / `llm.call`

### M5.2 LangSmith / Langfuse 集成
**文件**：`backend/app/observability/tracing.py`
```python
import os
def setup_tracing():
    if settings.langsmith_tracing:
        os.environ["LANGCHAIN_TRACING_V2"] = "true"
        os.environ["LANGCHAIN_API_KEY"] = settings.langsmith_api_key
        os.environ["LANGCHAIN_PROJECT"] = settings.langsmith_project
```
- `app/main.py` 启动时调用 `setup_tracing()`
- 验证：开 LANGSMITH_TRACING=true 跑一次研究请求 → LangSmith UI 能看到完整 trace（含 6 个 node + LLM 调用）
- 不开也不报错（优雅降级）

### M5.3 Prometheus 指标
**文件**：`backend/app/observability/metrics.py`
```python
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
REQUESTS = Counter("scholarai_requests_total", "Total requests", ["mode","endpoint","status"])
DURATION = Histogram("scholarai_request_duration_seconds", "Request duration", ["mode","endpoint"])
LLM_TOKENS = Counter("scholarai_llm_tokens_total", "LLM tokens", ["model","type"])
NODE_DURATION = Histogram("scholarai_node_duration_seconds", "Node duration", ["node"])
```
- 加 FastAPI middleware 自动计 REQUESTS / DURATION
- LangChain callback handler 抓 LLM token 用量（`get_openai_callback` 或自定义）
- 节点计时用 `time.perf_counter()` 包在 node 函数外

**端点**：
- `app/main.py` 加 `GET /metrics` 返回 `generate_latest()`

**验证**：
```bash
curl http://localhost:8000/metrics
# 看到 scholarai_requests_total、scholarai_node_duration_seconds_bucket 等
# 跑 1 次研究后这些指标有数值
```

### M5.4 调试入口：LangGraph Studio（可选）
- `langgraph.json` 配置文件，支持 `langgraph dev` 本地启 Studio
- 主要用于开发期，不进生产镜像
- 时间允许做，否则跳过（YAGNI）

✅ → **打 tag v0.1.0-m5**

**M5 退出检查**：
- [ ] 日志 JSON 格式、含 request_id
- [ ] LangSmith（如果开）能看到 6 个 node
- [ ] `/metrics` 暴露关键指标
- [ ] 一次研究请求能完整 trace 回放

---

## 7. M6 · 部署

### M6.1 Dockerfile（后端）
**文件**：`backend/Dockerfile`
```dockerfile
# 多阶段构建
FROM python:3.11-slim AS builder
WORKDIR /app
COPY pyproject.toml uv.lock* ./
RUN pip install uv && uv export --no-hashes -o requirements.txt && \
    pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

FROM python:3.11-slim
WORKDIR /app
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels /wheels/*
COPY app ./app
ENV PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**前端**：`frontend/Dockerfile`
```dockerfile
FROM node:20-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
```
`nginx.conf` 配置 SPA fallback + `/api` 反代到 backend。

### M6.2 docker-compose
**文件**：`deploy/docker-compose.yml`
```yaml
services:
  backend:
    build: ../backend
    env_file: ../.env
    ports: ["8000:8000"]
    volumes: ["../data:/app/data"]   # Chroma 嵌入式，持久化随 data/ 卷

  frontend:
    build: ../frontend
    ports: ["5173:80"]
    depends_on: [backend]
```

> ponytail: **不起独立 chroma 容器**。后端用嵌入式 `PersistentClient` 直接读写 `data/chroma/`，
> 容器化只需把 `data/` 挂进去。多 worker/多副本扩展时再切 client-server（见 §12.10）。
> 单 worker 部署：`uvicorn app.main:app --host 0.0.0.0 --port 8000`（**不加 `--workers`**，
> 嵌入式 Chroma 多进程写同一目录会冲突）。

### M6.3 一键启动验证
```bash
cd deploy
docker compose up -d --build
# 等 30s
curl http://localhost:8000/health   # {"ok":true}
curl http://localhost:8000/metrics  # 有 prom 输出
# 浏览器开 http://localhost:5173 → 走通问答 + 研究模式
docker compose down
```

### M6.4 云端部署文档
**文件**：`deploy/README.md`
- 三个选项详细步骤（每选项 ≤ 1 页）：
  1. **AWS Fargate**：ECR 推镜像 + task definition + ALB + EFS
  2. **GCP Cloud Run**：`gcloud run deploy` 一行命令
  3. **阿里云 SAE / 腾讯云轻量**：国内最方便
- 环境变量注入：用云厂商 Secret Manager / Parameter Store
- Chroma 持久化：云盘挂载，或用 Pinecone 替代
- PDF 存储：S3/OSS，boto3/oss2 替换本地 `PaperStorage` 接口

**不实现 K8s YAML**，但留 `deploy/k8s/` 目录 + README 占位。

✅ → **打 tag v0.1.0-m6 → 1.0.0-rc1**

**M6 退出检查**：
- [ ] `docker compose up` 一键起
- [ ] 三种部署方式文档可读、可执行
- [ ] README 有完整的本地启动 + 部署说明

---

## 8. 健壮性与降级设计（横切关注点）

> **适用方式**：每个阶段实现时把对应条目织入，不单独做。  
> **原则**：可恢复的失败 → 重试 / 降级；不可恢复的失败 → 友好报错 + 可定位日志；用户输入错误 → 400 + 明确提示；外部抖动 → 降级；核心依赖挂了 → 503。

### 8.1 统一异常体系

`backend/app/errors.py`：

```python
class ScholarAIError(Exception):
    code: str = "internal_error"
    status_code: int = 500
    def __init__(self, message: str, *, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}

class LLMUnavailable(ScholarAIError):        code, status = "llm_unavailable", 503
class LLMRateLimited(ScholarAIError):        code, status = "llm_rate_limited", 429
class LLMTimeout(ScholarAIError):            code, status = "llm_timeout",      504
class ArxivFetchFailed(ScholarAIError):      code, status = "arxiv_fetch_failed", 502
class PDFDownloadFailed(ScholarAIError):     code, status = "pdf_download_failed", 502
class PDFParseFailed(ScholarAIError):        code, status = "pdf_parse_failed", 422
class KnowledgeBaseNotFound(ScholarAIError): code, status = "kb_not_found", 404
class DocumentNotFound(ScholarAIError):      code, status = "doc_not_found", 404
class InvalidQuery(ScholarAIError):          code, status = "invalid_query", 400
class ResourceLimitExceeded(ScholarAIError): code, status = "resource_limit_exceeded", 413
class ChromaUnavailable(ScholarAIError):     code, status = "chroma_unavailable", 503
```

`backend/app/main.py` 全局 handler：

```python
from fastapi.responses import JSONResponse
from app.observability.logging import logger
from app.errors import ScholarAIError

@app.exception_handler(ScholarAIError)
async def scholarai_error_handler(request, exc):
    rid = getattr(request.state, "request_id", "-")
    logger.warning("api.error", code=exc.code, message=exc.message, request_id=rid, path=request.url.path)
    return JSONResponse(status_code=exc.status_code,
        content={"code": exc.code, "message": exc.message, "details": exc.details, "request_id": rid})

@app.exception_handler(Exception)
async def unhandled(request, exc):
    rid = getattr(request.state, "request_id", "-")
    logger.exception("api.unhandled", request_id=rid, path=request.url.path)
    return JSONResponse(status_code=500,
        content={"code":"internal_error","message":"服务内部错误","details":{},"request_id":rid})
```

### 8.2 LLM 调用韧性

`backend/app/llm.py`：

```python
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import openai, asyncio

RETRYABLE = (openai.APIConnectionError, openai.APITimeoutError, openai.InternalServerError, openai.RateLimitError)

@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10),
       retry=retry_if_exception_type(RETRYABLE), reraise=True)
async def call_llm(messages, *, timeout: float = 60.0, **kw):
    try:
        return await asyncio.wait_for(get_llm().ainvoke(messages, **kw), timeout=timeout)
    except asyncio.TimeoutError as e:
        raise LLMTimeout(f"LLM 调用超时（{timeout}s）") from e
    except openai.RateLimitError as e:
        raise LLMRateLimited("LLM 触发限流") from e
    except openai.APIError as e:
        raise LLMUnavailable(f"LLM 服务异常：{e}") from e
```

要点：
- **超时**：`asyncio.wait_for` 硬切（默认 60s，研究模式 Synthesizer 给 180s）
- **重试**：仅对网络/5xx，4xx 不重试（用户输入问题，重试无用）
- **降级**：第一版不做模型级降级（避免引入更难调的复杂性），只做 fail-fast + 友好错误
- **token 预算**：每次 invoke 显式传 `max_tokens`，研究模式按 max_papers 自动估算

### 8.3 外部 API 韧性

| 依赖 | 失败策略 |
|------|---------|
| arxiv 检索 | 单 query 失败 → 跳过该 query；整轮全失败 → 抛 `ArxivFetchFailed`（附最后一个原始错误） |
| PDF 下载 | tenacity 3 次重试 + 单篇失败不阻断；累积失败 >30% → 警告日志 |
| PDF 解析 | 单篇失败 → 跳过该篇，综述标注 "X 篇解析失败"；整批失败 → `PDFParseFailed` |
| Chroma 写入 | 失败必须回滚 Document.status='failed'，绝不留下"SQL 删了但向量还在"或反之的不一致 |
| Embedding API | 失败 → `ChromaUnavailable` 503，不返回半成品索引 |

### 8.4 输入校验与资源限制

`backend/app/schemas/common.py`：

```python
from pydantic import BaseModel, Field
from typing import Literal

class QueryRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    kb_ids: list[str] = Field(default_factory=list, max_length=10)

class ResearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    max_papers: int = Field(20, ge=1, le=100)
    depth: Literal["quick","normal","deep"] = "normal"

class UploadConstraints:
    MAX_PDF_SIZE = 50 * 1024 * 1024   # 50MB
    MAX_PAGES_PER_PDF = 500            # 防止恶意巨型 PDF
```

- FastAPI 上传：`request.headers.get("content-length")` 预检，超限直返 413
- 单 session 消息上限 1000，超出截断最早（避免内存爆）
- 速率限制：第一版不做（YAGNI），v0.2 加 `slowapi`，按 IP 限 60 req/min

### 8.5 分层健康检查

- `GET /health/live`：进程在 → `{"status":"alive"}`（K8s livenessProbe）
- `GET /health/ready`：DB `SELECT 1` + Chroma `client.heartbeat()` + 磁盘可写 → `{"status":"ready","db":"ok","chroma":"ok","disk":"ok"}`（readinessProbe）
- 任一依赖不通 → 503，让 LB / k8s 摘流量

### 8.6 优雅关闭

- uvicorn 默认等 in-flight 请求完成
- `atexit` 注册：关 Chroma client、关 SQLAlchemy engine、清 `UPLOAD_DIR` 临时文件
- 长任务（研究模式）：客户端断开 SSE 后**继续跑到当前节点结束**（半成品状态会落库，下次可重试）；下一节点不再触发
- 关键操作加锁用 `asyncio.Lock`，避免并发取消导致状态错乱

### 8.7 前端错误处理

- `<ErrorBoundary>` 包 `<App />`：崩了显示降级页 + "刷新" + "复制错误码"按钮
- `useSSE` hook：连接异常自动重连 1 次（指数退避），再失败 toast 提示
- 所有 mutation 按钮：`{loading, error, success}` 三态，错误用 toast（顶部红色条，3s 自动消失）
- 表单：提交按钮在 loading 时禁用并显示 spinner
- 长请求：`AbortController` + 60s 超时，用户可主动取消
- 文案（先中文，i18n 留 v0.2）：
  - `llm_unavailable` → "AI 服务暂时不可用，请稍后重试"
  - `arxiv_fetch_failed` → "arxiv 检索失败：{details.error}"
  - `pdf_parse_failed` → "PDF 解析失败，可能是扫描件或加密文件"
  - `kb_not_found` → "知识库不存在或已删除"
  - `resource_limit_exceeded` → "请求超出限制：{details.reason}"
- 每个 toast 带"复制错误码"按钮（含 `request_id`），用户反馈时直接定位

### 8.8 统一错误响应协议

所有 4xx/5xx：
```json
{
  "code": "kb_not_found",
  "message": "知识库不存在",
  "details": { "kb_id": "..." },
  "request_id": "uuid-v4"
}
```

- 前端按 `code` 分流（i18n + 兜底）
- 后端日志按 `request_id` 关联（FastAPI middleware 注入 → structlog context → LangChain metadata）

### 8.9 降级 vs 报错决策表

| 场景 | 策略 | 备注 |
|------|------|------|
| 单篇 PDF 下载失败 | **降级** | 跳过 + 综述标注 |
| 5 个 arxiv query 1 个空结果 | **降级** | 用其余 4 个继续 |
| Chroma 临时不可用 | **报错 503** | 不返回脏数据 |
| 用户传 100MB PDF | **报错 413** | 明确说"上限 50MB" |
| LLM 一直 500 | **报错 503** | 3 次重试后放弃 |
| 单篇 PDF 解析失败 | **降级** | 跳过 + 标注 |
| 用户 query 为空 | **报错 400** | `invalid_query` |
| 知识库不存在 | **报错 404** | `kb_not_found` |
| 同一 KB 内重复上传同名文件 | **报错 409** | 提示已存在，可选覆盖 |

### 8.10 织入时机

| 阶段 | 织入项 |
|------|--------|
| M1.4 | 建 `errors.py` + 全局 handler + 统一错误响应（§8.1, 8.8） |
| M2.4 | KB 上传/删除包 try/except + Chroma-SQL 一致性（§8.3） |
| M2.5 | 上传大小限制 + QueryRequest schema（§8.4） |
| M3.3 | 研究 endpoint 调 graph 外包 try/except + LLM 超时（§8.2） |
| M4.1 | Downloader 失败标注不阻断 + PDFParseFailed 兜底（§8.3） |
| M5.1 | 错误日志带 code + request_id（§8.8） |
| M5.3 | 错误计数加 Prometheus 指标（`scholarai_errors_total{code}`） |
| M6 | `/health/ready` 给云端探针用，docker compose healthcheck 用 `/health/live`（§8.5） |

每阶段"退出检查"末尾追加：
- [ ] 错误响应符合 §8.8 协议
- [ ] 关键失败有降级或友好报错（不能裸 500）
- [ ] 关键外部调用有超时 + 重试

---

## 9. 测试 / 验证清单汇总

每完成一个子阶段必须勾选：

| 阶段 | 单元测试 | 集成 / E2E | 手动 smoke |
|------|----------|-----------|-----------|
| M1.1 | `test_health.py` | - | curl `/health` |
| M1.2 | `App.test.tsx` 渲染 | - | 浏览器看页面 |
| M1.3 | `test_chat.py` mock | - | 浏览器收发消息 |
| M1.4 | mock LLM | - | 真实 DeepSeek 调用 |
| M2.1 | `test_models.py` | alembic upgrade/downgrade | sqlite 看表 |
| M2.2 | `test_vector_store.py` | - | 手动 add/query |
| M2.3 | `test_indexer.py` | - | 真实 PDF 索引 |
| M2.4 | `test_kb_api.py` | curl 全套端点 | 浏览器管理 KB |
| M2.5 | mock embeddings/LLM | - | 引用可见 |
| M3.x | `test_research_graph.py` | mock LLM 跑通 | 浏览器研究模式 |
| M4.x | `test_downloader.py` `test_analyzer.py` | 真实 1 篇 arxiv | 真实综述生成 |
| M5.x | 日志格式 / metrics 暴露 | LangSmith trace | 一次研究回看 |
| M6.x | - | `docker compose up` 全栈 smoke | 三模式全跑通 |

---

## 10. 风险与决策日志

| 日期 | 决策 | 原因 |
|------|------|------|
| 2026-07-16 | 单仓 monorepo 而非多仓 | 一个人维护，PR/issue 简单 |
| 2026-07-16 | 开发用 DeepSeek v4-flash，生产预留多 provider | 用户当前 key 选 DeepSeek，LangChain 抽象层切；v4-flash 用户已验证可用 |
| 2026-07-16 | Embedding 用本地 BGE-small-zh | DeepSeek 无 embedding API；本地零额外 key；模型 ~100MB 一次下载 |
| 2026-07-16 | 问答带多轮 history | UX 关键；token 成本用 LLM_TIMEOUT + max_tokens 控制 |
| 2026-07-16 | 阶段 6 不写 K8s YAML | 部署方式 3 选 1，先云厂商托管服务 |
| 2026-07-16 | Chroma 持久化用本地目录，不用 client/server 模式 | 单机够用，docker 起一个 container 共享卷 |
| 2026-07-16 | 第一版单用户，session 不加 auth | YAGNI；预留 `user_id` 字段 |
| 2026-07-16 | PDF 上传走 BackgroundTasks 异步 | 同步索引会让客户端超时；轮询 status |
| 2026-07-16 | 研究模式同 session 串行 | DB 唯一约束，避免重复研究浪费 token |
| 2026-07-16 | LangGraph 用 MemorySaver 起，留接口切 Postgres | 第一版单机够，后期零业务代码升级 |
| 2026-07-16 | LangSmith 采样率 env 控制 | dev 1.0 / prod 0.1，平衡 debug 与额度 |
| 2026-07-16 | 设置页 `/settings` 推迟 v0.2 | 单人开发，配置走 `.env`/`apikey.txt` 够用 |
| 2026-07-16 | QA 模式保持简单 RAG，不做 ReAct agent | 检索→拼 context→LLM 已满足；web/arxiv 工具留 v0.2 |
| 2026-07-16 | Chroma 用嵌入式 `PersistentClient` + **单 worker** | 单机单人 YAGNI；多 worker 需切 HttpClient（升级路径见 §12.10）|
| 2026-07-16 | M6 compose 删掉独立 `chroma` 容器 | 嵌入式模式下容器用不上，避免误导 |
| 2026-07-16 | 研究综述只做 step+final，不做 token 流式 | 实现简单；打字机效果留 v0.2 |
| 2026-07-16 | 综述额外落盘 `data/reports/{session_id}.md` | 方便下载/缓存/复盘 |
| 2026-07-16 | M5 不做 OpenTelemetry | LangSmith 覆盖 LLM trace，structlog+Prometheus 覆盖应用层 |
| 2026-07-16 | RAG 不做 MMR 重排；长文不做 Map-Reduce | 先 cosine top-k + 截断前 8000 token，质量不够再升级 |

---

## 12. 补遗：开发期补充事项

> 本节是 review 后发现、原方案漏掉或描述不足的项。每个子阶段实现时按需取用，不强制全做。

### 12.1 M1.2 · 前端 shadcn + 路径别名

`M1.2` 起步时：
```bash
cd frontend
npx shadcn@latest init -d   # 默认配置
npx shadcn@latest add button input dialog toast card separator scroll-area dropdown-menu
```

`tsconfig.json` 加 path alias：
```json
{
  "compilerOptions": {
    "strict": true,
    "baseUrl": ".",
    "paths": { "@/*": ["./src/*"] }
  }
}
```
`vite.config.ts` 同步加 `resolve.alias`。

所有组件 import 一律 `@/components/...`，不要写 `../../`。

### 12.2 M1.3 · Session 标题自动生成

`POST /api/chat/qa` 第一次调用时（session 不存在）：
- 创建 Session，title 临时填 `"新对话"`
- LLM 生成 ≤10 字标题：`await llm.ainvoke([sys("把用户问题总结成 ≤10 字标题，只输出标题文本"), user(query)])`
- 更新 title

成本可控（~50 token/次），UX 提升明显。

### 12.3 M1.4 · 问答多轮历史管理

修改 `app/api/chat.py`：
- 查 session 的最近 N=10 条 message（user/assistant 交替）
- 构造 LangChain messages：
  ```python
  history = await db.get_recent_messages(session_id, limit=10)
  messages = [SystemMessage(content=QA_SYSTEM_PROMPT)]
  for m in history: messages.append(HumanMessage(content=m.content) if m.role=="user" else AIMessage(content=m.content))
  messages.append(HumanMessage(content=current_query))
  ```
- 调 LLM，存新的 user/assistant message 回 DB
- System prompt 固定：
  ```
  你是 ScholarAI 学术助手。回答简洁、准确，引用知识库时用 [来源编号]。
  ```

### 12.4 M2.4 · PDF 上传安全 + 异步索引

**安全**：
```python
PDF_MAGIC = b"%PDF-"
async def validate_pdf(content: bytes) -> None:
    if not content.startswith(PDF_MAGIC): raise InvalidQuery("不是合法 PDF 文件")
    if len(content) > settings.upload_max_size_mb * 1024 * 1024: raise ResourceLimitExceeded("文件超限")
```
- 文件名用 `uuid7().hex + ".pdf"`，**不信任用户输入的文件名**
- 路径用 `UPLOAD_DIR / f"{doc_id}.pdf"`，杜绝 `../` 穿越

**异步索引**（PDF > 5 页要走后台）：
```python
from fastapi import BackgroundTasks
@router.post("/{kb_id}/docs")
async def upload_doc(kb_id: str, file: UploadFile, bg: BackgroundTasks, db = Depends(get_db)):
    content = await file.read()
    validate_pdf(content)
    doc = Document(kb_id=kb_id, filename=file.filename, status="pending")
    db.add(doc); await db.commit()
    target_path = settings.upload_dir / f"{doc.id}.pdf"
    target_path.write_bytes(content)
    bg.add_task(index_pdf_task, doc.id, kb_id, str(target_path))   # 异步
    return {"doc_id": doc.id, "status": "pending"}
```
- 客户端轮询 `GET /api/knowledge/{kb_id}/docs/{doc_id}` 拿 status：`pending → indexed / failed`
- 前端上传后显示 spinner，完成切到 indexed（成功）或显示重试按钮（失败）

### 12.5 M3.1 · LangGraph Checkpointer 抽象

第一版默认用 `MemorySaver`（重启丢），但要在 `research_graph.py` 留出接口便于切换：

```python
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.base import BaseCheckpointSaver
def build_graph(checkpointer: BaseCheckpointSaver | None = None) -> CompiledGraph:
    g = StateGraph(ResearchState)
    # ... nodes & edges ...
    return g.compile(checkpointer=checkpointer or MemorySaver())
```

后期切 Postgres：装 `langgraph-checkpoint-postgres`，加 connection，零业务代码改动。

### 12.6 M5.2 · LangSmith 采样率

```python
# app/observability/tracing.py
import random
def maybe_enable_tracing():
    if not settings.langsmith_tracing: return
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGCHAIN_API_KEY"] = settings.langsmith_api_key
    if settings.langchain_sample_rate < 1.0:
        # 自定义 sampler：decorator 包裹节点调用，按概率决定是否 trace
        ...
```
- v0.1 dev 用 1.0（所有调用都 trace，方便 debug）
- v0.2 prod 用 0.1（10%），省 LangSmith 额度

### 12.7 横切 · 仓库顶层文件

仓库根需要：
- `README.md`：项目介绍、特性、Quick Start（`make up`）、架构图、Roadmap
- `LICENSE`：MIT（默认）/ Apache 2.0（更严）/ 内部（看用户需要）
- `.editorconfig`：缩进、换行统一
- `.pre-commit-config.yaml`：跑 ruff / eslint / prettier

M1.0 起步时一并加上。

### 12.8 横切 · 同一 session 并发控制

研究模式：同一 session_id 同一时刻只允许 1 个研究跑。

```python
# app/api/research.py
from sqlalchemy.exc import IntegrityError
@router.post("")
async def start_research(req: ResearchRequest, db = Depends(get_db)):
    # 在 session 表加 status 列：idle | running | done | failed
    session = await db.get(Session, req.session_id)
    if session.status == "running":
        raise ResourceLimitExceeded("该会话已有研究在进行中", details={"session_id": str(req.session_id)})
    session.status = "running"
    try:
        await db.commit()
    except IntegrityError:
        raise ResourceLimitExceeded("并发冲突")
    # ... 启动研究
```

跑完（成功/失败）都把 status 改回。

### 12.9 横切 · README 启动指南（最小版）

M1 起步时写的 `README.md` 应包含：
1. 项目简介（一段话 + 架构图）
2. 快速开始：
   ```bash
   git clone ...
   cd ScholarAI
   make init-env    # 读 apikey.txt 写 .env
   make up-be       # 启后端
   make up-fe       # 启前端
   # 浏览器开 http://localhost:5173
   ```
3. 架构图（贴 ASCII 或指向 docs/）
4. 开发路线（指向 DEVELOPMENT_PLAN.md）

不要在 M1 写完美 README，骨架就够，发布前再补。

### 12.10 未来扩展 · Chroma 切 client-server（多 worker / 多副本）

> 现在不做。触发条件：需要 `uvicorn --workers N`、多容器副本，或向量数据要独立于后端生命周期。

嵌入式 `PersistentClient` 的限制：多进程写同一目录会冲突，所以生产单 worker。要扩展时按下面切，**业务代码零改动**（只动 `vector_store.py` + config + compose）：

1. `config.py` 加 `chroma_host: str = ""`、`chroma_port: int = 8000`
2. `vector_store.py` 的 `get_client()`：
   ```python
   if settings.chroma_host:
       return chromadb.HttpClient(host=settings.chroma_host, port=settings.chroma_port)
   return chromadb.PersistentClient(path=settings.chroma_persist_dir)  # 降级：本地开发
   ```
3. compose 加回 chroma 服务：
   ```yaml
   chroma:
     image: chromadb/chroma:latest
     ports: ["8001:8000"]
     volumes: ["chroma_data:/chroma/chroma"]
   volumes:
     chroma_data:
   ```
   backend 加 `environment: [CHROMA_HOST=chroma]` + `depends_on: [chroma]`，即可 `--workers N`。
4. 云端可进一步换托管向量库（Pinecone/阿里云），仅替换 `get_client()` 实现。

---

## 11. 立即开始

```bash
# 1. 初始化仓库（5 min）
cd /home/gh/ScholarAI
git init
# 写 .gitignore / .env.example / Makefile
git add . && git commit -m "chore: initial repo scaffolding"

# 2. 装 Python 工具链
curl -LsSf https://astral.sh/uv/install.sh | sh
# 或 sudo apt install python3.11 + pip

# 3. 装 Node 工具链（已装则跳过）
node -v   # 应 ≥ 20

# 4. 跑 M1.1
mkdir backend && cd backend
uv init
# 写 app/main.py app/config.py
uv add fastapi uvicorn pydantic pydantic-settings
uv run uvicorn app.main:app --reload
# 另一终端 curl localhost:8000/health
```

> 任何阶段卡住就停下，把报错贴出来，我们再决定怎么修。**不要硬撞过测试**。

---

附录：完成后会得到的功能（v1.0-rc1）
- ✅ 浏览器开箱即用，React 侧边栏 + 问答/研究双模式
- ✅ 知识库管理 + RAG 引用
- ✅ 一句话生成论文综述（含方法/实验抽取）
- ✅ LangSmith 全链路 trace + Prometheus 指标
- ✅ 一行 `docker compose up` 拉起完整栈
- ✅ 三种云端部署文档
