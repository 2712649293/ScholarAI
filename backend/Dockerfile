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
# uv.lock 钉死 torch==x.y.z（PyPI 默认 CUDA 版，几百 MB）。改钉 +cpu 轮子（PyTorch 官方 CPU index），
# 顺带丢弃可能的平台 marker（本镜像只跑 linux）
RUN uv export --no-hashes --no-dev -o /tmp/requirements.txt && \
    sed -i 's/^torch==\([0-9.]*\).*$/torch==\1+cpu/' /tmp/requirements.txt && \
    pip wheel --no-cache-dir --wheel-dir /wheels \
        --extra-index-url https://download.pytorch.org/whl/cpu \
        -r /tmp/requirements.txt

# === 阶段 2: 预下载 BGE 模型（需要在网络下）===
# ponytail: 只装 huggingface_hub 下模型（不带 torch），产出标准 HF 缓存目录布局，
# runtime 的 SentenceTransformer 离线时按 ~/.cache/huggingface/hub 查找
FROM python:3.11-slim AS model-loader

RUN pip install --no-cache-dir huggingface_hub

# 此阶段必须能联网（HF 默认源）。离线环境可设 HF_ENDPOINT=https://hf-mirror.com
ARG BGE_MODEL=BAAI/bge-small-zh-v1.5
RUN python -c "from huggingface_hub import snapshot_download; snapshot_download('${BGE_MODEL}', cache_dir='/models')"

# === 阶段 3: runtime（无网络）===
FROM python:3.11-slim

WORKDIR /app

COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir --no-index --find-links=/wheels /wheels/* && \
    rm -rf /wheels

# 预下载的模型 → appuser 默认 HF 缓存目录（~/.cache/huggingface/hub；app 未显式传 cache_directory）
# 必须在 /root 之外：runtime 以 appuser 运行，读不了 /root
COPY --from=model-loader /models /home/appuser/.cache/huggingface/hub

COPY app ./app
COPY alembic.ini entrypoint.sh ./
RUN chmod +x entrypoint.sh

# 非 root。模型缓存目录也要 chown（COPY 时属主是 root，加载模型时可能写锁文件）
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app /home/appuser/.cache
USER appuser

ENV PYTHONUNBUFFERED=1 \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

EXPOSE 8000

# ponytail: 不加 --workers！嵌入式 Chroma 多进程写同一目录会冲突
#   扩展到多 worker 时切换 Chroma 为 HttpClient（client-server 模式），见 §12.10
# entrypoint 先 alembic upgrade head（空 data 卷时建表），再 exec 原命令
ENTRYPOINT ["./entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
