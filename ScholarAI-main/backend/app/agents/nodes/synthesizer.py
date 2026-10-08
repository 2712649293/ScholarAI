"""Synthesizer：纯函数，读 state dict 返回更新 dict（不关心 langgraph，工具包 Command）。"""
from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.state import ResearchState
from app.llm import call_llm

SYSTEM = (
    "你是学术综述撰写助手。基于给定论文的结构化分析（或摘要），用中文写一篇结构化综述，"
    "包含章节：引言、研究现状、方法分类、趋势、未来方向。"
    "每条关键结论用 [arxiv_id] 标注来源。只依据给定内容，不要编造。"
    "如果提供了论文下载状态列表，在综述末尾按'参考文献'格式列出每篇论文的 arxiv_id、"
    "标题及下载状态（✅ 已下载 / ❌ 下载失败需自行查阅）。"
)


def _format_papers(papers: list[dict], analyses: list[dict]) -> str:
    by_id = {a["arxiv_id"]: a for a in analyses}
    blocks: list[str] = []
    for p in papers:
        a = by_id.get(p["arxiv_id"])
        if a:  # 有结构化分析，优先用
            blocks.append(
                f"[{p['arxiv_id']}] {p['title']}\n"
                f"问题：{a['problem']}\n方法：{a['method']}\n结果：{a['results']}\n"
                f"局限：{a['limitations']}\n新意：{a['novelty']}"
            )
        else:  # 退回 abstract
            blocks.append(f"[{p['arxiv_id']}] ({p['year']}) {p['title']}\n摘要：{p['abstract']}")
    return "\n\n".join(blocks)


async def run(state: ResearchState) -> dict:
    """返回 state 更新 dict（不含 messages，由工具加 ToolMessage）。"""
    iteration = state.get("iteration", 0) + 1
    papers = state.get("papers", [])
    if not papers:
        return {"draft": "未检索到相关论文，无法生成综述。", "iteration": iteration}

    context = _format_papers(papers, state.get("analyses", []))
    user = (
        f"研究方向：{state['query']}\n\n"
        + "子问题：\n"
        + "\n".join(f"- {q}" for q in state.get("sub_questions", []))
        + f"\n\n论文资料（共 {len(papers)} 篇）：\n{context}"
    )
    # M5.5.11: 传下载成功/失败论文列表，让 LLM 在综述末尾列出
    failures = state.get("download_failures", [])
    downloaded = [p for p in papers if p.get("local_path")]
    if failures:
        user += (
            f"\n\n**论文下载状态**：\n"
            f"✅ 成功下载 {len(downloaded)} 篇：\n"
            + "\n".join(f"- [{p['arxiv_id']}] {p['title']}" for p in downloaded)
            + f"\n\n❌ 下载失败 {len(failures)} 篇（PDF 不可达，需自行查阅）：\n"
            + "\n".join(f"- [{aid}]（需自行查阅）" for aid in failures)
            + "\n\n请在综述末尾按'参考文献'格式列出每篇论文的 arxiv_id、标题及下载状态（✅/❌）。"
        )
    feedback = state.get("feedback", "")
    existing_draft = (state.get("draft") or "").strip()
    if feedback and existing_draft:
        # Refine 模式
        user += (
            f"\n\n【上一版综述（需改写）】\n{existing_draft}\n\n"
            f"【修改要求】\n{feedback}\n\n"
            "请只按修改要求改写，其他部分尽量保持原文。返回完整新版本。"
        )
    elif feedback:
        user += f"\n\n请基于以下要求生成综述：\n{feedback}"

    messages = [SystemMessage(content=SYSTEM), HumanMessage(content=user)]
    draft = await call_llm(messages, timeout=180)
    return {"draft": draft, "iteration": iteration}
