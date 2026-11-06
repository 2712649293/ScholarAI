"""研究入口的轻量意图分类。

RAG 问答入口不使用这里的分类器；这里仅决定研究模式内是启动新研究，
还是把输入交给带研究会话上下文的对话 Agent。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Intent = Literal["chat", "followup", "research"]


@dataclass(frozen=True)
class IntentResult:
    intent: Intent
    confidence: float


_RESEARCH_MARKERS = (
    "研究", "综述", "调研", "检索", "论文", "文献", "进展", "趋势", "找几篇",
    "查找", "调查", "比较方法", "研究报告", "系统综述",
)
_CHAT_MARKERS = (
    "你好", "hello", "hi", "谢谢", "早上好", "晚安", "哈哈", "天气",
    "什么是", "如何", "为什么", "怎么理解", "解释", "区别", "能否", "请问",
)
_FOLLOWUP_MARKERS = (
    "继续", "刚才", "上面", "上一轮", "这个结论", "这篇", "再解释", "修改综述",
    "补充", "展开", "换个说法", "你提到", "基于这份综述",
)


def requested_research_mode(query: str) -> Literal["quick", "deep"]:
    """提取用户明确措辞，未说明时默认快速研究。"""
    text = query.strip().lower()
    deep = ("深度研究", "深入研究", "深度分析", "详细分析", "完整分析", "逐篇分析", "下载论文")
    return "deep" if any(marker in text for marker in deep) else "quick"


def classify_intent(query: str, *, has_research_context: bool = False) -> Intent:
    text = query.strip().lower()
    if has_research_context and (
        any(marker in text for marker in _FOLLOWUP_MARKERS)
        or not any(marker in text for marker in _RESEARCH_MARKERS)
    ):
        return "followup"
    if any(marker in text for marker in _CHAT_MARKERS) and not any(
        marker in text for marker in _RESEARCH_MARKERS
    ):
        return "chat"
    if any(marker in text for marker in _RESEARCH_MARKERS):
        return "research"
    # 新研究常常只有一个主题短语（如“RAG”）；在无上下文时不能把它误判成问答。
    return "followup" if has_research_context else "research"


def classify_request(query: str, *, has_research_context: bool = False) -> IntentResult:
    intent = classify_intent(query, has_research_context=has_research_context)
    if intent != "research":
        return IntentResult(intent=intent, confidence=0.8)
    return IntentResult(intent=intent, confidence=0.5)
