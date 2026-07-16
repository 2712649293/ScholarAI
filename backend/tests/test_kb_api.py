"""知识库 API 冒烟测试。"""
from __future__ import annotations

import tempfile
import time
from pathlib import Path

import pymupdf
from fastapi.testclient import TestClient

from app.main import app
from app.db.session import SessionLocal
from app.db.models import KnowledgeBase, Document

client = TestClient(app)


def _make_pdf_bytes(pages_text: list[str]) -> bytes:
    doc = pymupdf.open()
    for text in pages_text:
        page = doc.new_page()
        page.insert_text((50, 50), text, fontsize=11)
    buf = doc.tobytes()
    doc.close()
    return buf


def test_create_list_get_delete_kb() -> None:
    r = client.post("/api/knowledge", json={"name": f"kb-test-{time.time_ns()}"})
    assert r.status_code == 201, r.text
    kb = r.json()
    kid = kb["id"]
    assert kb["name"].startswith("kb-test-")
    assert kb["vector_count"] == 0

    r = client.get("/api/knowledge")
    assert r.status_code == 200
    assert any(k["id"] == kid for k in r.json())

    r = client.get(f"/api/knowledge/{kid}")
    assert r.status_code == 200
    assert r.json()["id"] == kid

    r = client.delete(f"/api/knowledge/{kid}")
    assert r.status_code == 204
    r = client.get(f"/api/knowledge/{kid}")
    assert r.status_code == 404


def test_duplicate_kb_name_409() -> None:
    name = f"dup-kb-{time.time_ns()}"
    r1 = client.post("/api/knowledge", json={"name": name})
    assert r1.status_code == 201
    r2 = client.post("/api/knowledge", json={"name": name})
    assert r2.status_code == 409
    # 清理
    client.delete(f"/api/knowledge/{r1.json()['id']}")


def test_upload_invalid_pdf_rejected() -> None:
    r = client.post("/api/knowledge", json={"name": f"kb-bad-{time.time_ns()}"})
    kid = r.json()["id"]
    try:
        # 上传非 PDF
        r = client.post(
            f"/api/knowledge/{kid}/docs",
            files={"file": ("fake.pdf", b"not a pdf content", "application/pdf")},
        )
        assert r.status_code == 400
        assert r.json()["detail"]["code"] == "invalid_pdf"
    finally:
        client.delete(f"/api/knowledge/{kid}")


def test_upload_valid_pdf_and_search() -> None:
    r = client.post("/api/knowledge", json={"name": f"kb-upload-{time.time_ns()}"})
    kid = r.json()["id"]
    try:
        pdf_bytes = _make_pdf_bytes([
            "LangChain 是一个 LLM 应用框架。它包含 chains、agents、memory 等模块。",
            "RAG 检索增强生成结合了检索器和语言模型，提升事实准确性。",
        ])
        r = client.post(
            f"/api/knowledge/{kid}/docs",
            files={"file": ("paper.pdf", pdf_bytes, "application/pdf")},
        )
        assert r.status_code == 201, r.text
        doc_id = r.json()["id"]
        assert r.json()["status"] == "pending"

        # 等后台任务完成
        deadline = time.time() + 30
        while time.time() < deadline:
            r = client.get(f"/api/knowledge/{kid}/docs/{doc_id}")
            if r.json()["status"] in ("indexed", "failed"):
                break
            time.sleep(0.5)
        assert r.json()["status"] == "indexed", r.json()

        # 测试检索
        r = client.post(
            f"/api/knowledge/{kid}/search",
            json={"query": "RAG 检索增强", "k": 3},
        )
        assert r.status_code == 200
        hits = r.json()
        assert len(hits) > 0
        assert any("RAG" in h["text"] or "检索" in h["text"] for h in hits)
    finally:
        client.delete(f"/api/knowledge/{kid}")


def test_search_on_nonexistent_kb_404() -> None:
    r = client.post("/api/knowledge/nonexistent-id-xxx/search", json={"query": "x", "k": 3})
    assert r.status_code == 404
