"""Synthesizer：纯函数，读 state dict 返回更新 dict（不关心 langgraph，工具包 Command）。"""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any
from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.state import ResearchContext
from app.llm import call_llm
from app.search.models import citation_id, paper_key

SYSTEM = (
    "你是学术综述撰写助手。基于给定论文的结构化分析（或摘要），用中文写一篇结构化综述，"
    "包含章节：引言、研究现状、方法分类、趋势、未来方向。"
    "每条关键结论用 [论文引用标识] 标注来源。只依据给定内容，不要编造。"
    "只依据传入的论文资料和结构化分析生成综述。下载失败的论文仅有摘要证据，"
    "必须标注仅依据摘要，不能声称已阅读全文或补写摘要未提供的实验细节。"
)


def analysis_for(paper: dict, analyses: list[dict]) -> dict | None:
    keys = {paper_key(paper), paper.get("paper_key"), paper.get("arxiv_id")} - {None, ""}
    return next((a for a in analyses if a.get("status") != "failed" and
                 keys & {a.get("paper_key"), a.get("arxiv_id")}), None)


def evidence_papers(state: Mapping[str, Any]) -> list[dict]:
    """Deep evidence requires analysis or an actual download failure with an abstract."""
    papers = state.get("papers", [])
    if "selected_paper_keys" in state:
        selected = set(state.get("selected_paper_keys") or [])
        papers = [p for p in papers if selected &
                  {paper_key(p), p.get("paper_key"), p.get("arxiv_id")}]
    if state.get("research_mode") != "deep":
        return list(papers)
    failures = set(state.get("download_failures", []))
    return [p for p in papers if (
        p.get("local_path") and analysis_for(p, state.get("analyses", []))
    ) or (
        not p.get("local_path") and str(p.get("abstract") or "").strip() and
        (p.get("download_status") == "failed" or
         failures & {paper_key(p), p.get("paper_key"), p.get("arxiv_id")})
    )]


def _format_papers(papers: list[dict], analyses: list[dict], *, allow_abstract: bool = True) -> str:
    blocks: list[str] = []
    for p in papers:
        identifier = citation_id(p)
        a = analysis_for(p, analyses)
        if a and a.get("status") != "failed":  # 有结构化分析，优先用
            evidence = "\n".join(
                f"- p{item.get('page')}: {item.get('claim', '')} | {item.get('quote', '')}"
                for item in a.get("evidence", [])
                if item.get("claim") or item.get("quote")
            )
            sections = "\n".join(
                f"- {item.get('heading', '')}: {item.get('summary', '')}"
                for item in a.get("section_analyses", [])
                if item.get("summary")
            )
            blocks.append(
                f"[{identifier}] {p['title']}（全文分析）\n"
                f"问题：{a.get('problem', '')}\n方法：{a.get('method', '')}\n结果：{a.get('results', '')}\n"
                f"局限：{a.get('limitations', '')}\n新意：{a.get('novelty', '')}\n"
                f"贡献：{'；'.join(a.get('contributions', []))}\n"
                f"数据集：{'；'.join(a.get('datasets', []))}\n"
                f"指标：{'；'.join(a.get('metrics', []))}\n"
                f"可复现性：{a.get('reproducibility', '')}\n"
                f"章节摘要：\n{sections}\n"
                f"证据：\n{evidence}"
            )
        elif allow_abstract:
            blocks.append(f"[{identifier}] ({p.get('year', '')}) {p['title']}（仅依据摘要）\n摘要：{p.get('abstract', '')}")
    return "\n\n".join(blocks)


async def run(state: ResearchContext) -> dict:
    """返回 state 更新 dict（不含 messages，由工具加 ToolMessage）。"""
    iteration = state.get("iteration", 0) + 1
    papers = state.get("papers", [])
    if not papers:
        return {"draft": "未检索到相关论文，无法生成综述。", "iteration": iteration}

    analyses = state.get("analyses", [])
    papers = evidence_papers(state)
    if not papers:
        return {"draft": "没有可用的论文分析或下载失败论文的摘要，无法生成综述。", "iteration": iteration}
    context = _format_papers(papers, analyses)
    user = (
        f"研究方向：{state['query']}\n\n"
        + "子问题：\n"
        + "\n".join(f"- {q}" for q in state.get("sub_questions", []))
        + f"\n\n论文资料（共 {len(papers)} 篇）：\n{context}"
    )
    if state.get("plan"):
        user += "\n\n用户已批准的研究计划（请严格遵守主题与章节大纲）：\n" + json.dumps(state["plan"], ensure_ascii=False)
    fallback = [p for p in papers if not p.get("local_path")] if state.get("research_mode") == "deep" else []
    if fallback:
        user += "\n\n下载失败的论文仅使用摘要；请明确区分全文分析和摘要证据。"
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
    if fallback:
        draft += "\n\n**证据说明：**以下论文下载失败，仅依据摘要，未进行全文分析：" + "；".join(
            f"[{citation_id(p)}] {p['title']}" for p in fallback
        ) + "。"
    return {"draft": draft, "iteration": iteration}
