# 后端 Dockerfile：多阶段构建。
#
# ponytail: 关键设计：
# 1. 镜像自包含 BGE embedding 模型（独立预下载阶段，runtime 强制 HF 离线不联网）
# 2. 单 worker（嵌入式 Chroma 多进程写同一目录会冲突，扩展见 docs/§12.10）
# 3. 非 root 用户跑

# === 阶段 1: 装依赖到 wheel ===
FROM python:3.11-slim AS builder

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml uv.lock* ./
RUN uv export --no-hashes --no-dev -o /tmp/requirements.txt && \
    pip wheel --no-cache-dir --wheel-dir /wheels -r /tmp/requirements.txt

# === 阶段 2: 预下载 BGE 模型（需要在网络下）===
FROM python:3.11-slim AS model-loader

RUN pip install --no-cache-dir sentence-transformers

# 此阶段必须能联网（HF 默认源）。离线环境可设 HF_ENDPOINT=https://hf-mirror.com
ARG BGE_MODEL=BAAI/bge-small-zh-v1.5
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('${BGE_MODEL}', cache_directory='/models')"

# === 阶段 3: runtime（无网络）===
FROM python:3.11-slim

WORKDIR /app

COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels /wheels/* && \
    rm -rf /wheels

# 预下载的模型（runtime 强制 HF_HUB_OFFLINE 才会用本地 cache）
COPY --from=model-loader /models /root/.cache/huggingface/hub

COPY app ./app

# 非 root
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

ENV PYTHONUNBUFFERED=1 \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

EXPOSE 8000

# ponytail: 不加 --workers！嵌入式 Chroma 多进程写同一目录会冲突
#   扩展到多 worker 时切换 Chroma 为 HttpClient（client-server 模式），见 §12.10
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
