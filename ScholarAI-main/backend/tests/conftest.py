"""测试全局 fixture / 环境设置。

关键：在任何 `app.*` 被 import 之前，把数据库和数据目录指向临时目录，
让测试彻底隔离，绝不污染 dev 的 data/scholarai.db 与 data/reports 等。
conftest 由 pytest 最先加载，所以此处设的 env 在 app.config.Settings() 实例化前生效。
"""
import atexit
import os
import shutil
import tempfile
from pathlib import Path

# 避免 sentence-transformers 联网检查更新导致测试变慢 / 失败
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

# 测试隔离：独立临时目录 + 临时 SQLite（覆盖 .env，pydantic-settings 中 env 优先级高于 .env）
_TEST_DIR = Path(tempfile.mkdtemp(prefix="scholarai-test-"))
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DIR / 'test.db'}"
os.environ["REPORTS_DIR"] = str(_TEST_DIR / "reports")
os.environ["PAPER_STORAGE_DIR"] = str(_TEST_DIR / "papers")
os.environ["UPLOAD_DIR"] = str(_TEST_DIR / "uploads")
os.environ["CHROMA_PERSIST_DIR"] = str(_TEST_DIR / "chroma")

atexit.register(lambda: shutil.rmtree(_TEST_DIR, ignore_errors=True))

import pytest  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _init_test_db():
    """在临时 DB 上按模型建表（测试不跑 alembic）。"""
    import app.db.models  # noqa: F401  确保 4 张表注册到 Base.metadata
    from app.db.session import Base, engine

    Base.metadata.create_all(engine)
    yield
    engine.dispose()


@pytest.fixture
def client():
    """M4.5.1: TestClient 触发 lifespan → init_saver 在 TestClient 的 event loop 跑。
    异步测试需要此 fixture；其他测试仍可独立用 TestClient(app)（不调 /api/research）。
    """
    from pathlib import Path
    from fastapi.testclient import TestClient
    from app.main import app
    from app.agents import research_agent

    target = Path(_TEST_DIR / "checkpoints.db")
    original = research_agent.CHECKPOINTS_DB
    research_agent.CHECKPOINTS_DB = target
    with TestClient(app) as c:
        yield c
    research_agent.CHECKPOINTS_DB = original
