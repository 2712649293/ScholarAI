"""Synthesizer 节点：基于结构化分析（有则）或 abstract 写综述（M3.2 / M4.3 升级）。"""
from __future__ import annotations

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.state import ResearchState
from app.llm import call_llm

SYSTEM = (
    "你是学术综述撰写助手。基于给定论文的结构化分析（或摘要），用中文写一篇结构化综述，"
    "包含章节：引言、研究现状、方法分类、趋势、未来方向。"
    "每条关键结论用 [arxiv_id] 标注来源。只依据给定内容，不要编造。"
)


def _format_papers(papers: list[dict], analyses: list[dict]) -> str:
    by_id = {a["arxiv_id"]: a for a in analyses}
    blocks: list[str] = []
    for p in papers:
        a = by_id.get(p["arxiv_id"])
        if a:  # M4：有结构化分析，优先用
            blocks.append(
                f"[{p['arxiv_id']}] {p['title']}\n"
                f"问题：{a['problem']}\n方法：{a['method']}\n结果：{a['results']}\n"
                f"局限：{a['limitations']}\n新意：{a['novelty']}"
            )
        else:  # 退回 abstract（下载/解析失败，或 M3 无分析）
            blocks.append(f"[{p['arxiv_id']}] ({p['year']}) {p['title']}\n摘要：{p['abstract']}")
    return "\n\n".join(blocks)


async def run(state: ResearchState) -> ResearchState:
    iteration = state.get("iteration", 0) + 1  # 单一自增点（供 Reviewer 循环判上限）
    papers = state.get("papers", [])
    if not papers:
        return {"report_draft": "未检索到相关论文，无法生成综述。", "iteration": iteration}

    context = _format_papers(papers, state.get("analyses", []))
    user = (
        f"研究方向：{state['query']}\n\n"
        + "子问题：\n"
        + "\n".join(f"- {q}" for q in state.get("sub_questions", []))
        + f"\n\n论文资料（共 {len(papers)} 篇）：\n{context}"
    )
    failures = state.get("download_failures", [])
    if failures:
        user += f"\n\n注意：有 {len(failures)} 篇论文下载失败，请在综述末尾注明。"
    feedback = state.get("feedback", "")
    if feedback:  # M4.3 重写：带上审校意见
        user += f"\n\n上一版综述的审校意见（请针对性改进）：\n{feedback}"

    messages = [SystemMessage(content=SYSTEM), HumanMessage(content=user)]
    draft = await call_llm(messages, timeout=180)
    return {"report_draft": draft, "iteration": iteration}
