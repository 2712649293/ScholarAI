# ScholarAI

论文问答与调研应用，前端使用 React / TypeScript / Vite / Tailwind，后端使用 FastAPI / LangChain / LangGraph。

## 当前能力

- **问答**：多轮对话，可选择多个知识库检索，返回带文档与页码的引用。
- **知识库**：创建、删除、上传 PDF、后台索引、重试与检索；文档状态为 `pending` / `indexed` / `failed`。
- **研究**：方向澄清 → 生成计划 → 用户编辑、批准或拒绝 → ReAct agent 按需检索 arXiv、下载、分析、撰写与审校；支持追问、改写及 Markdown 导出。批准后的执行与追问不因切换会话中止，切回自动恢复进度；服务重启后的未完成研究可手动从检查点继续。
- **持久化与观测**：SQLite 保存会话和知识库元数据，独立 SQLite 保存 LangGraph 状态，Chroma 保存向量；提供结构化日志、Prometheus 指标及可选 LangSmith tracing。

当前边界：研究检索源为 arXiv，澄清时确认的起止年份用于检索过滤，未澄清的直接研究接口默认从 2018 年开始；PDF 下载或分析失败时可退回摘要综述。PDF 使用 PyMuPDF4LLM 转 Markdown，可选图片描述、章节与语义分块、关键词+向量混合检索及外部 rerank，无 OCR。使用与服务协议见 [RAG 说明](docs/rag.md)。部署约束见[部署说明](deploy/README.md)。

## 本地启动

需要 Python 3.11（见 `backend/.python-version`）、uv、Node.js 22（≥22.13）或 24 LTS，以及 npm。以下命令在仓库根目录执行：

```bash
cp .env.example backend/.env
# 编辑 backend/.env，填写 LLM_API_KEY，并确认 LLM_BASE_URL / LLM_MODEL
make install-be
make install-fe
```

LLM 统一通过 `ChatOpenAI` 调用 OpenAI 兼容服务；研究模式需要模型支持工具调用和结构化输出。`LLM_PROVIDER` 当前不参与客户端选择。

默认 embedding 为本地 BGE。应用强制离线加载模型，首次使用须先单独下载到当前用户的 Hugging Face 缓存；自定义模型时将下方名称与 `BGE_MODEL` 保持一致：

```bash
(cd backend && env -u HF_HUB_OFFLINE -u TRANSFORMERS_OFFLINE uv run python -c "from huggingface_hub import snapshot_download; snapshot_download('BAAI/bge-small-zh-v1.5')")
```

下载源不可达时，先在终端执行 `export HF_ENDPOINT=https://hf-mirror.com`，再运行下载命令。使用远程 embedding 时跳过下载，配置见下节。

在两个终端分别执行：

```bash
make up-be  # 先执行 Alembic 迁移，再启动后端 http://localhost:8000
make up-fe  # 前端 http://localhost:5173，/api 代理至后端
```

直接运行 Uvicorn 时，先在 `backend/` 执行 `uv run alembic upgrade head`。`make init-env` 仅适用于已有未跟踪的 `apikey.txt`（含 `apikey:` 和 `model:`）的环境；手动复制示例配置即可启动。

## 配置与数据

配置字段以 [Settings](backend/app/config.py) 为准，可复制的示例集中在 [.env.example](.env.example)。环境变量优先于 `backend/.env`；默认相对路径基于后端进程的工作目录，本地数据写入 `backend/data/`。

使用远程 embedding 时，将 `EMBEDDING_PROVIDER` 改为 `remote`，取消注释 `EMBEDDING_API_KEY`、`EMBEDDING_BASE_URL`、`EMBEDDING_MODEL` 并填写服务实际值。`openai` / `qwen` / `remote` / `openai-compatible` 使用同一 OpenAI 兼容实现；仅填写 URL 不会切换 provider。URL 填服务基础地址（通常到 `/v1`），不要填完整 `/embeddings` 路径。兼容别名见配置示例。

**更换 embedding 模型名后，下次启动会删除模型名不匹配的知识库、向量及配置目录内的关联 PDF，需要重新上传。** 清理按模型名判断，同名服务或向量维度变化不会自动识别。

## 代码入口与开发

| 位置 | 职责 |
| --- | --- |
| `backend/app/main.py`、`api/`、`schemas/` | 应用启动、HTTP / SSE 接口和请求校验 |
| `backend/app/agents/` | 澄清、计划暂停与恢复、研究工具及持久化状态 |
| `backend/app/rag/` | Markdown/图片、语义分块、embedding、混合检索与重排 |
| `backend/app/db/`、`session_store.py` | 业务数据与 Alembic 迁移 |
| `backend/app/observability/` | 日志、指标和 tracing |
| `frontend/src/` | 页面、交互和 API 封装，见[前端说明](frontend/README.md) |

接口字段与响应以运行中的 [OpenAPI 文档](http://localhost:8000/docs) 为准。研究 SSE 的事件处理见 `frontend/src/lib/api.ts` 和后端 `api/research.py`、`api/clarify.py`。

```bash
make test-be          # pytest；默认排除真实模型/网络的 integration
make test-fe          # Vitest
make type-be          # mypy
```

仅运行非 integration 测试：`cd backend && uv run pytest -m 'not integration'`；真实外部调用用 `uv run pytest -m integration`，需要对应服务与凭据。前端构建和 lint 命令见前端说明。

## 文档维护

- 根 README 负责项目现状与启动；前端 README 负责前端开发；部署 README 负责运行与存储；配置示例负责环境变量。相同信息只维护一处，其余使用链接。
- 功能变化时替换对应公开说明并删除失效内容，不把每轮过程记录追加到公开 README。
- 本地开发文档放在被 Git 忽略的 `dev-docs/`，每个功能版本一个目录；当前版本为 `v002-rag-upgrade/`，`v001-current-code/` 保留为基线。后续开发同步更新功能、接口、修复、验证和待办，维护约定见 [AGENTS.md](AGENTS.md)。
- 接口、依赖版本与默认值引用代码或 OpenAPI；尚未接入的配置明确标注，不写成已实现能力。
- 根 README 控制在约 100 行，前端和部署说明各约 60 行以内；超出时先删重复与过时内容，再决定是否拆分。
