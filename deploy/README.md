# ScholarAI 部署

当前提供 Docker Compose（后端单 worker + nginx 前端），未提供 Kubernetes 清单。源码启动和环境配置见[根 README](../README.md)。

## 启停

先创建并填写 `backend/.env`。在仓库根目录执行：

```bash
make docker-up      # 构建并启动，前端 http://localhost:5173
make docker-logs    # 跟踪日志
make docker-down    # 停止服务，保留宿主机数据
```

后端入口先执行 `alembic upgrade head`。构建需要访问 Python / npm 依赖源及 Hugging Face；镜像预下载默认 BGE 和 tiktoken 分词缓存，运行时仅 Hugging Face 模型加载离线，LLM、远程 embedding、可选 vision/rerank 和论文检索 / 下载仍需要网络。

修改 `BGE_MODEL` 时也需匹配构建参数，在 `deploy/` 执行 `docker compose build --build-arg BGE_MODEL=模型名称 backend`，再执行 `docker compose up -d`；默认 `make docker-up` 不传该参数。

## 存储与代理

[docker-compose.yml](docker-compose.yml) 将仓库根目录 `data/` 挂载到后端 `/app/data`，覆盖 `.env` 中的数据库、Chroma、上传、论文和报告路径；LangGraph 的 `checkpoints.db` 也位于该目录。本地源码模式默认使用 `backend/data/`，两者是不同数据目录。备份应在停止后端后复制整个对应目录。

nginx 配置位于 [frontend/nginx.conf](../frontend/nginx.conf)，提供 SPA 路由回退、`/api/` 和 `/metrics` 代理，关闭 SSE 缓冲，读取 / 发送超时为 900 秒；上传请求体上限为 50 MiB。增加上传大小需同时修改 nginx 和 `UPLOAD_MAX_SIZE_MB`。

## 检查与运行约束

| 后端地址 | 含义 |
| --- | --- |
| `http://localhost:8000/health/live` | 进程存活；Compose 使用此检查 |
| `http://localhost:8000/health/ready` | 业务表、Chroma、上传目录可写检查；失败返回 503 |
| `http://localhost:8000/metrics` | Prometheus 指标 |
| `http://localhost:8000/docs` | OpenAPI 接口文档 |

`/health/ready` 不检查 LLM、embedding、vision 或 rerank 服务，存活也不代表外部服务可用；通过前端端口访问 `/health/*` 会落入 SPA 回退，应直接检查后端端口。

- 当前使用嵌入式 Chroma、SQLite 检查点和进程内研究任务注册表，保持单 worker / 单副本。配置 PostgreSQL URL 不会自动替换 SQLite 检查点，也未配套数据库驱动与部署。
- PDF 索引由进程内 `BackgroundTasks` 执行，无持久化任务队列。重启后未完成文档标为失败，可在页面重新处理；索引与删除通过单进程锁互斥。
- 已批准研究与追问的 SSE 断开仅取消订阅，后台继续执行；服务退出会停止任务，启动时将遗留 running 状态标为 failed，用户可从待执行检查点手动恢复。事件缓存不跨进程重启保留，已完成报告与消息仍落盘。未接入持久任务队列或自动重试；恢复可能再次调用尚未提交结果的外部工具。
- `RESEARCH_TIMEOUT_SECONDS` 尚未接入研究总超时；当前由各工具的超时和 agent 步数上限约束，nginx 超时不等于任务取消。
- 论文检索和 PDF 下载由后端依赖中的 `paper-search-mcp` MCP 子进程执行；如改用独立命令或服务，在后端环境配置 `PAPER_SEARCH_MCP_COMMAND` / `PAPER_SEARCH_MCP_ARGS`，并确保容器可访问对应网络和凭据。
- 更换 embedding 的清理行为见根 README。`make clean` 会删除根目录 `data/` 等目录，不用于日常停服。

迁移到 Kubernetes 前需落实持久卷、凭据注入和上述探针；多副本还需调整 Chroma、检查点与任务执行方式。

论文 Markdown、图片及缓存与 PDF 一并放在持久化上传目录下，删除文档时清理。新 RAG 表与 FTS5 由 Alembic 建立；旧库需删后重新上传，见 [RAG 使用说明](../docs/rag.md)。
