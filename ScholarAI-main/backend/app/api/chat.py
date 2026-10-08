"""问答模式 API：M1.4 多轮 history + M2.5 知识库 RAG。"""
from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.llm import call_llm
from app.rag import indexer
from app.schemas.chat import ChatRequest, ChatResponse, Citation
from app.session_store import store

QA_SYSTEM_PROMPT = (
    "你是 ScholarAI 学术助手。回答简洁、准确。"
    "如果用户引用了知识库内容，请用 [来源编号] 标注，编号对应下面的'参考资料'。"
    "如果知识库中没有相关信息，直接回答，不要编造。"
)

MAX_CITATIONS = 5  # 单次回复最多带 5 条引用

router = ...  # placeholder
from fastapi import APIRouter  # noqa: E402

router = APIRouter(prefix="/api/chat", tags=["chat"])


def _build_rag_context(kb_ids: list[str], query: str) -> tuple[str, list[Citation]]:
    """从指定 KB 拉取 top-K 命中，组成 context 文本 + 引用列表。"""
    all_hits: list[dict] = []
    for kb_id in kb_ids:
        try:
            hits = indexer.search(kb_id, query, k=MAX_CITATIONS)
            for h in hits:
                all_hits.append({"kb_id": kb_id, **h})
        except Exception:
            continue  # 单 KB 失败不阻断；§8.3 降级
    # 按 score 降序，取 top MAX_CITATIONS
    all_hits.sort(key=lambda x: x.get("score", 0), reverse=True)
    top = all_hits[:MAX_CITATIONS]

    if not top:
        return "", []

    lines = ["参考资料："]
    citations: list[Citation] = []
    for i, h in enumerate(top, start=1):
        lines.append(
            f"[{i}] (kb:{h['kb_id']}, doc:{h['doc_id']}, p:{h['page']}) {h['text'][:300]}"
        )
        citations.append(Citation(
            kb_id=h["kb_id"],
            doc_id=h["doc_id"],
            page=h["page"],
            chunk_index=h["chunk_index"],
            text=h["text"][:300],
            score=h["score"],
        ))
    return "\n".join(lines), citations


@router.post("/qa", response_model=ChatResponse)
async def qa(req: ChatRequest) -> ChatResponse:
    """问答模式：可选挂载知识库（RAG），多轮 history。"""
    session = store.get_or_create(req.session_id)
    history = store.recent(session.id, n=req.max_history)

    # RAG 检索
    rag_context, citations = _build_rag_context(req.kb_ids, req.query)

    # 拼 system prompt
    system_content = QA_SYSTEM_PROMPT
    if rag_context:
        system_content = f"{QA_SYSTEM_PROMPT}\n\n{rag_context}"

    messages: list = [SystemMessage(content=system_content)]
    for m in history:
        if m.role == "user":
            messages.append(HumanMessage(content=m.content))
        elif m.role == "assistant":
            messages.append(AIMessage(content=m.content))
    messages.append(HumanMessage(content=req.query))

    reply = await call_llm(messages)

    store.add_message(session.id, "user", req.query)
    store.add_message(session.id, "assistant", reply)

    return ChatResponse(reply=reply, session_id=session.id, echo=False, citations=citations)
