"""知识库 CRUD + PDF 上传 + 检索测试。"""
from __future__ import annotations

import uuid
import asyncio
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import Document, KnowledgeBase
from app.db.session import get_db
from app.rag import indexer, keyword_store, vector_store as vs
from app.rag.embeddings import embedding_model_name
from app.rag.cleanup import remove_document_files
from app.schemas.knowledge import (
    DocResponse,
    KBCreate,
    KBResponse,
    SearchHit,
    SearchRequest,
)

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])

PDF_MAGIC = b"%PDF-"
CHUNK_SIZE = 1024 * 1024  # 1MB 读块


def _ensure_upload_dir() -> Path:
    p = Path(settings.upload_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _persist_uploaded_doc(kb_id: str, doc_id: str, filename: str, target: Path) -> Document:
    """Recheck the KB after the streamed upload, serialized with KB deletion."""
    from app.db.session import SessionLocal
    with indexer.kb_lock(kb_id), SessionLocal() as db:
        kb = db.get(KnowledgeBase, kb_id)
        if kb is None:
            raise HTTPException(404, detail={"code": "kb_not_found", "message": "知识库已删除"})
        doc = Document(id=doc_id, kb_id=kb_id, filename=filename,
                       file_path=str(target), status="pending")
        db.add(doc)
        db.flush()
        kb.doc_count = db.query(Document).filter_by(kb_id=kb_id).count()
        db.commit()
        db.refresh(doc)
        return doc


def _index_pdf_task(doc_id: str, kb_id: str, pdf_path: str) -> None:
    with indexer.kb_lock(kb_id):
        _run_index_pdf_task(doc_id, kb_id, pdf_path)


def _run_index_pdf_task(doc_id: str, kb_id: str, pdf_path: str) -> None:
    """后台索引任务：调 indexer，更新 Document.status。"""
    from app.db.session import SessionLocal
    with SessionLocal() as db:
        if db.get(Document, doc_id) is None or db.get(KnowledgeBase, kb_id) is None:
            return
        try:
            res = indexer.index_pdf(kb_id, pdf_path, doc_id=doc_id, require_existing=True)
            db.expire_all()
            # 上传后的后台索引可能与删除重叠；不能留下重新创建的向量集合。
            if db.get(KnowledgeBase, kb_id) is None:
                vs.delete_collection(kb_id)
                return
            doc = db.get(Document, doc_id)
            if doc is not None:
                if res.chunk_count == 0:
                    # 0 chunk = 解析失败（可能是扫描件 / 加密 / 抽不出文本）
                    doc.status = "failed"
                    doc.error = "PDF 解析成功但未提取到文本（可能是扫描件或加密 PDF）"
                else:
                    doc.status = "indexed"
                    doc.error = None
                    doc.page_count = res.page_count
                    doc.indexed_at = datetime.now(timezone.utc)
            kb = db.get(KnowledgeBase, kb_id)
            if kb is not None:
                kb.doc_count = db.query(Document).filter_by(kb_id=kb_id).count()
                kb.vector_count = keyword_store.chunk_count(kb_id)
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
    kb = KnowledgeBase(name=req.name, embedding_model=embedding_model_name())
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
    # Count published chunks, excluding failed/obsolete generation remnants.
    kb.vector_count = keyword_store.chunk_count(kb_id)
    return kb


@router.delete("/{kb_id}", status_code=204)
def delete_kb(kb_id: str, db: Session = Depends(get_db)) -> None:
    kb = db.get(KnowledgeBase, kb_id)
    if kb is None:
        raise HTTPException(status_code=404, detail={"code": "kb_not_found", "message": "知识库不存在"})
    with indexer.kb_lock(kb_id):
        db.expire_all()
        kb = db.get(KnowledgeBase, kb_id)
        if kb is None:
            raise HTTPException(404, detail={"code": "kb_not_found", "message": "知识库不存在"})
        vs.delete_collection(kb_id)
        keyword_store.delete_kb(kb_id, db)
        remove_document_files(kb)
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

    doc_id = uuid.uuid4().hex
    upload_dir = _ensure_upload_dir()
    target = upload_dir / f"{doc_id}.pdf"
    size = 0
    try:
        with target.open("wb") as output:
            while chunk := await file.read(CHUNK_SIZE):
                size += len(chunk)
                if size > settings.upload_max_size_mb * 1024 * 1024:
                    raise HTTPException(413, detail={"code": "file_too_large", "message": f"文件超过 {settings.upload_max_size_mb}MB 上限"})
                if output.tell() == 0 and not chunk.startswith(PDF_MAGIC):
                    raise HTTPException(400, detail={"code": "invalid_pdf", "message": "不是合法 PDF 文件"})
                output.write(chunk)
            if size == 0:
                raise HTTPException(400, detail={"code": "invalid_pdf", "message": "PDF 文件为空"})
    except BaseException:
        target.unlink(missing_ok=True)
        raise

    try:
        doc = await asyncio.to_thread(_persist_uploaded_doc, kb_id, doc_id,
                                      file.filename or f"{doc_id}.pdf", target)
    except Exception:
        target.unlink(missing_ok=True)
        raise
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

@router.post("/{kb_id}/docs/{doc_id}/retry", response_model=DocResponse, status_code=202)
def retry_doc(kb_id: str, doc_id: str, background: BackgroundTasks,
              db: Session = Depends(get_db)) -> Document:
    with indexer.kb_lock(kb_id):
        doc = db.get(Document, doc_id)
        if doc is None or doc.kb_id != kb_id:
            raise HTTPException(404, detail={"code": "doc_not_found", "message": "文档不存在"})
        if doc.status == "pending":
            raise HTTPException(409, detail={"code": "index_running", "message": "文档正在处理中"})
        doc.status, doc.error, doc.processing_stage, doc.warnings = "pending", None, "pending", "[]"
        db.commit()
        db.refresh(doc)
        background.add_task(_index_pdf_task, doc.id, kb_id, doc.file_path)
        return doc


@router.delete("/{kb_id}/docs/{doc_id}", status_code=204)
def delete_doc(kb_id: str, doc_id: str, db: Session = Depends(get_db)) -> None:
    from sqlalchemy import delete
    from app.db.models import RAGChunk, RAGGeneration
    import shutil
    with indexer.kb_lock(kb_id):
        doc = db.get(Document, doc_id)
        if doc is None or doc.kb_id != kb_id:
            raise HTTPException(404, detail={"code": "doc_not_found", "message": "文档不存在"})
        vs.get_or_create_collection(kb_id).delete(where={"doc_id": doc_id})
        db.execute(delete(RAGChunk).where(RAGChunk.doc_id == doc_id))
        db.execute(delete(RAGGeneration).where(RAGGeneration.doc_id == doc_id))
        path = Path(doc.file_path).resolve()
        roots = [Path(settings.upload_dir).resolve(), Path(settings.paper_storage_dir).resolve()]
        if any(root in path.parents for root in roots):
            path.unlink(missing_ok=True)
        shutil.rmtree(indexer.artifact_root(doc_id), ignore_errors=True)
        db.delete(doc)
        db.flush()
        kb = db.get(KnowledgeBase, kb_id)
        if kb:
            kb.doc_count = db.query(Document).filter_by(kb_id=kb_id).count()
            # Use this transaction's view after deleting the document's chunks.
            from sqlalchemy import func
            kb.vector_count = int(db.scalar(select(func.count()).select_from(RAGChunk).where(RAGChunk.kb_id == kb_id)) or 0)
        db.commit()

@router.post("/{kb_id}/search", response_model=list[SearchHit])
def search_kb(kb_id: str, req: SearchRequest, db: Session = Depends(get_db)) -> list[dict]:
    if db.get(KnowledgeBase, kb_id) is None:
        raise HTTPException(status_code=404, detail={"code": "kb_not_found", "message": "知识库不存在"})
    return indexer.search(kb_id, req.query, k=req.k)
