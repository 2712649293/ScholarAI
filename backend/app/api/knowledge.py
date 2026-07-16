"""知识库 CRUD + PDF 上传 + 检索测试。"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import Document, KnowledgeBase
from app.db.session import get_db
from app.rag import indexer, vector_store as vs
from app.schemas.knowledge import (
    DocResponse,
    KBCreate,
    KBResponse,
    SearchHit,
    SearchRequest,
)

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

PDF_MAGIC = b"%PDF-"
CHUNK_SIZE = 1024 * 1024  # 1MB 读块


def _ensure_upload_dir() -> Path:
    p = Path(settings.upload_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _validate_pdf(content: bytes) -> None:
    if not content.startswith(PDF_MAGIC):
        raise HTTPException(status_code=400, detail={"code": "invalid_pdf", "message": "不是合法 PDF 文件"})
    if len(content) > settings.upload_max_size_mb * 1024 * 1024:
        raise HTTPException(
            status_code=413,
            detail={"code": "file_too_large", "message": f"文件超过 {settings.upload_max_size_mb}MB 上限"},
        )


def _index_pdf_task(doc_id: str, kb_id: str, pdf_path: str) -> None:
    """后台索引任务：调 indexer，更新 Document.status。"""
    from app.db.session import SessionLocal
    with SessionLocal() as db:
        try:
            res = indexer.index_pdf(kb_id, pdf_path, doc_id=doc_id)
            doc = db.get(Document, doc_id)
            if doc is not None:
                doc.status = "indexed"
                doc.page_count = res.page_count
                doc.indexed_at = datetime.now(timezone.utc)
            kb = db.get(KnowledgeBase, kb_id)
            if kb is not None:
                kb.doc_count = db.query(Document).filter_by(kb_id=kb_id).count()
                kb.vector_count = vs.collection_count(kb_id)
            db.commit()
        except Exception as e:
            db.rollback()
            with SessionLocal() as db2:
                doc = db2.get(Document, doc_id)
                if doc is not None:
                    doc.status = "failed"
                    doc.error = str(e)[:500]
                db2.commit()


# === KB CRUD ===

@router.post("", response_model=KBResponse, status_code=201)
def create_kb(req: KBCreate, db: Session = Depends(get_db)) -> KnowledgeBase:
    existing = db.scalar(select(KnowledgeBase).where(KnowledgeBase.name == req.name))
    if existing is not None:
        raise HTTPException(status_code=409, detail={"code": "kb_exists", "message": f"KB '{req.name}' 已存在"})
    kb = KnowledgeBase(name=req.name, embedding_model=settings.bge_model)
    db.add(kb)
    db.commit()
    db.refresh(kb)
    return kb


@router.get("", response_model=list[KBResponse])
def list_kbs(db: Session = Depends(get_db)) -> list[KnowledgeBase]:
    return list(db.scalars(select(KnowledgeBase).order_by(KnowledgeBase.created_at.desc())).all())


@router.get("/{kb_id}", response_model=KBResponse)
def get_kb(kb_id: str, db: Session = Depends(get_db)) -> KnowledgeBase:
    kb = db.get(KnowledgeBase, kb_id)
    if kb is None:
        raise HTTPException(status_code=404, detail={"code": "kb_not_found", "message": "知识库不存在"})
    # 实时同步 vector_count（chroma 是 source of truth）
    kb.vector_count = vs.collection_count(kb_id)
    return kb


@router.delete("/{kb_id}", status_code=204)
def delete_kb(kb_id: str, db: Session = Depends(get_db)) -> None:
    kb = db.get(KnowledgeBase, kb_id)
    if kb is None:
        raise HTTPException(status_code=404, detail={"code": "kb_not_found", "message": "知识库不存在"})
    vs.delete_collection(kb_id)
    db.delete(kb)  # cascade 删 documents
    db.commit()


# === Documents ===

@router.post("/{kb_id}/docs", response_model=DocResponse, status_code=201)
async def upload_doc(
    kb_id: str,
    background: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> Document:
    kb = db.get(KnowledgeBase, kb_id)
    if kb is None:
        raise HTTPException(status_code=404, detail={"code": "kb_not_found", "message": "知识库不存在"})

    content = await file.read()
    _validate_pdf(content)

    doc_id = uuid.uuid4().hex
    upload_dir = _ensure_upload_dir()
    target = upload_dir / f"{doc_id}.pdf"
    target.write_bytes(content)

    doc = Document(
        id=doc_id,
        kb_id=kb_id,
        filename=file.filename or f"{doc_id}.pdf",
        file_path=str(target),
        status="pending",
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    background.add_task(_index_pdf_task, doc_id, kb_id, str(target))
    return doc


@router.get("/{kb_id}/docs", response_model=list[DocResponse])
def list_docs(kb_id: str, db: Session = Depends(get_db)) -> list[Document]:
    if db.get(KnowledgeBase, kb_id) is None:
        raise HTTPException(status_code=404, detail={"code": "kb_not_found", "message": "知识库不存在"})
    return list(db.scalars(select(Document).where(Document.kb_id == kb_id).order_by(Document.created_at.desc())).all())


@router.get("/{kb_id}/docs/{doc_id}", response_model=DocResponse)
def get_doc(kb_id: str, doc_id: str, db: Session = Depends(get_db)) -> Document:
    doc = db.get(Document, doc_id)
    if doc is None or doc.kb_id != kb_id:
        raise HTTPException(status_code=404, detail={"code": "doc_not_found", "message": "文档不存在"})
    return doc


# === Search ===

@router.post("/{kb_id}/search", response_model=list[SearchHit])
def search_kb(kb_id: str, req: SearchRequest, db: Session = Depends(get_db)) -> list[dict]:
    if db.get(KnowledgeBase, kb_id) is None:
        raise HTTPException(status_code=404, detail={"code": "kb_not_found", "message": "知识库不存在"})
    return indexer.search(kb_id, req.query, k=req.k)
