"""问答模式 API（M1.3：mock，M1.4 接 DeepSeek）。"""
from __future__ import annotations

from fastapi import APIRouter

from app.schemas.chat import ChatRequest, ChatResponse

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("/qa", response_model=ChatResponse)
def qa(req: ChatRequest) -> ChatResponse:
    """问答模式（M1.3 mock：直接回显）。"""
    return ChatResponse(reply=f"你问的是：{req.query}", echo=True)
