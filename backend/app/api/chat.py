"""问答模式 API（M1.4：接 DeepSeek，多轮 history）。"""
from __future__ import annotations

from fastapi import APIRouter
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.llm import call_llm
from app.schemas.chat import ChatRequest, ChatResponse
from app.session_store import store

QA_SYSTEM_PROMPT = (
    "你是 ScholarAI 学术助手。回答简洁、准确。"
    "如果用户引用了知识库内容，请用 [来源编号] 标注。"
)

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/qa", response_model=ChatResponse)
async def qa(req: ChatRequest) -> ChatResponse:
    """问答模式：取最近 max_history 条历史 + 新 query → LLM。"""
    session = store.get_or_create(req.session_id)
    history = store.recent(session.id, n=req.max_history)

    messages: list = [SystemMessage(content=QA_SYSTEM_PROMPT)]
    for m in history:
        if m.role == "user":
            messages.append(HumanMessage(content=m.content))
        elif m.role == "assistant":
            messages.append(AIMessage(content=m.content))
    messages.append(HumanMessage(content=req.query))

    reply = await call_llm(messages)

    store.add_message(session.id, "user", req.query)
    store.add_message(session.id, "assistant", reply)

    return ChatResponse(reply=reply, session_id=session.id, echo=False)
