"""测试全局 fixture / 环境设置。"""
import os

# 避免 sentence-transformers 联网检查更新导致测试变慢 / 失败
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
