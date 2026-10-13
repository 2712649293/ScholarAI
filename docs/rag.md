# 论文 RAG 使用说明

适用于有文本层的 PDF，包括常见两栏论文；本版本关闭 OCR，纯扫描件会明确失败。

## 导入与产物

上传 → PyMuPDF4LLM 1.28.2 转 Markdown、提取栅格/矢量图 → 可选图片描述 → Markdown 章节/段落 + embedding 语义边界分块 → Chroma 与 SQLite FTS5/BM25 双索引。

解析产物在 `UPLOAD_DIR/rag/<doc_id>/<generation>/`：`paper.extracted.md` 为库的原始输出，`paper.raw.md` 为链接规范化后的 Markdown，`paper.enriched.md` 含可选图片描述，`images/` 保存图像，`manifest.json` 保存布局、图片状态与警告，`chunks.json` 保存分块、来源及索引配置指纹。PDF 保持在上传目录。图片引用保留原位置和作者图注；生成描述标记为 `AI 图片描述`，不可视作作者原文。

每块保存章节、页码范围、来源跨度与图号。默认长度为最小120、目标300、最大450 token；最多重叠一个完整句子、60 token。章节优先，长表格按行拆分并重复表头；超长图片/公式块仍需按模型上限拆分。本地 BGE 使用实际 tokenizer 与 `max_seq_length`；远程服务用 cl100k_base 估算，需将 `EMBEDDING_MAX_INPUT_TOKENS` 设置为服务允许的保守预算。超限文本拒绝静默截断。完整 Markdown 保留用于核对复杂表格与公式。

## 可选图片描述

`backend/.env` 的 `VISION_*` 与普通 LLM 配置独立。例如：

```dotenv
VISION_BASE_URL=https://your-vision-host/v1
VISION_MODEL=gpt-6.1-sol
VISION_API_KEY=your-key
VISION_API_STYLE=responses
```

`gpt-6.1-sol` 是本项目用户指定的目标 API 模型；实际服务需提供该模型的图片输入能力。URL 填基础地址，代码追加 `/responses`；`chat_completions` 模式追加 `/chat/completions`。请求包含 base64 图像、原图注和邻近正文，分别使用 Responses 的 `input_image` 或 Chat Completions 的 `image_url`。

留空 URL 不调用模型，图像和图注继续进入处理链路。无认证本地端点可留空 key。默认每图45秒、每文档总预算180秒、并发2、最多30张；失败或达到预算保留原图继续索引。缓存按图片、图注、上下文、模型、服务地址和提示版本区分，修改这些内容后重新处理会重新生成。配置字段见 [.env.example](../.env.example)。

## 混合检索与外部重排

所选知识库范围内分别召回最多30条向量候选与30条关键词候选；中文使用 jieba 分词，英文保留专业术语/版本号。按 chunk ID 去重，以 RRF（常数60）融合，最多40条进入可选外部 rerank。单个支路失败使用另一路，两路均失败返回 `retrieval_unavailable` / HTTP503。相同名称但 provider、端点或维度不同的旧向量不参与当前向量检索，关键词仍可使用。

配置独立的完整 `RERANK_ENDPOINT`、可选 `RERANK_MODEL` / `RERANK_API_KEY`。支持常见如下协议，**不能把普通 chat URL 当作 rerank 服务**：

```json
{"query":"question","documents":["chunk A","chunk B"],"top_n":2,"model":"optional-model"}
```

响应：

```json
{"results":[{"index":1,"relevance_score":0.92},{"index":0,"relevance_score":0.31}]}
```

`index` 对应发送的 documents 数组。超时、非法索引/分数或空结果均保留 RRF 排序，部分返回不会丢掉其余候选。`score` 为 RRF 融合分数，`rerank_score` 单独返回；不同服务分数不应直接比较。更换 rerank 不需重新索引。

问答统一检索所选知识库，最多6条引用，按总计4000 token预算放入完整证据块，避免截掉每块300字符后的信息。引用保留页码范围、章节及图号；引用预览可短于模型接收的正文。

## 删除、重试与升级

每次重建先写新代际向量，再在同一 SQLite 事务内写 chunks/FTS 并切换发布指针；失败保持上次发布的数据可检索。后台任务仍在单进程内运行，重启时把未完成任务标为失败，可重新处理。删除文档/知识库会清理双索引、上传 PDF、Markdown、图片与缓存。

- `POST /api/knowledge/{kb_id}/docs/{doc_id}/retry`：重新处理；pending 返回409。
- `DELETE /api/knowledge/{kb_id}/docs/{doc_id}`：删除独立文档。
- 页面提供重新处理、删除、处理提示与引用定位；API详情以 OpenAPI 为准。

先执行 `cd backend && uv run python -m alembic upgrade head`。旧版分块/向量不兼容新索引，本开发版本不迁移旧文档；已有旧库应删除后重新上传。只针对业务文档清理，不删除会话或本地开发版本记录。

复杂论文布局、子图覆盖、图片描述准确性与检索参数仍需用真实论文及服务评估；普通测试中的外部模型响应为模拟数据。研究 analyzer 共用 Markdown 解析，但仍只读取前20000字符，未复用知识库图片增强和长文分段分析。
