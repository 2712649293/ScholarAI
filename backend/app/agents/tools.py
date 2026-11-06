"""研究子 agent = tool（M4.5.1：langgraph 标准格式）。

每个 tool 接收 `ToolRuntime` 访问 state，**用 `Command(update=...)` 写回**。
不依赖闭包/ResearchContext，state 全部在 langgraph state（checkpointer 持久化）。
半约束：前置校验在 tool 里，乱序调用返回错误让 agent 自纠。
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from langchain.tools import tool, ToolRuntime
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from app.agents.nodes import analyzer, downloader, synthesizer
from app.agents.state import ResearchContext
from app.observability.logging import logger
from app.search import SearchAgent
from app.search.models import citation_id, paper_key


_SEARCH_IN_FLIGHT: set[str] = set()


async def _search_command(queries: list[str], runtime: ToolRuntime) -> Command:
    state = runtime.state
    attempts = int(state.get("search_attempts", 0))
    if attempts >= 1 and state.get("papers"):
        return Command(
            update={
                "messages": [
                    _tool_message(
                        runtime,
                        "当前研究轮次已经有检索结果，请直接调用 select_papers；如需换关键词，请在研究完成后发起新的追问。",
                    )
                ]
            }
        )
    if attempts >= 2:
        return Command(
            update={
                "messages": [
                    _tool_message(runtime, "本轮检索次数已达上限，请使用当前结果继续后续研究步骤。")
                ]
            }
        )
    search_key = str(state.get("session_id") or f"runtime:{id(runtime)}")
    if search_key in _SEARCH_IN_FLIGHT:
        return Command(
            update={
                "messages": [
                    _tool_message(runtime, "同一研究轮次已有检索正在执行，请等待该结果后继续。")
                ]
            }
        )
    new_queries = list(dict.fromkeys(state.get("search_queries", []) + queries))
    search_state = {
        "query": state.get("clarified_query") or state.get("query", ""),
        "search_queries": queries,
        "max_papers": state.get("max_papers", 20),
        "clarify_direction": state.get("clarify_direction"),
    }
    _SEARCH_IN_FLIGHT.add(search_key)
    try:
        result = await SearchAgent().run(search_state)
        logger.info(
            "research.search.completed",
            session_id=state.get("session_id"),
            search_backend="paper-search-mcp",
            sources=result.sources,
            query_count=len(result.queries),
            paper_count=len(result.papers),
            status=result.status,
            source_errors=result.source_errors,
        )
        if result.status == "failed":
            detail = "; ".join(f"{k}: {v}" for k, v in result.source_errors.items())
            prefix = "arxiv 检索失败" if result.sources == ["arxiv"] else "论文检索失败"
            return Command(
                update={
                    "search_queries": new_queries,
                    "search_attempts": attempts + 1,
                    "messages": [
                        _tool_message(runtime, f"{prefix}：{detail}。建议修改查询词或稍后重试。")
                    ],
                }
            )
        return Command(
            update={
                "search_queries": new_queries,
                "search_attempts": attempts + 1,
                "papers": result.papers,
                "messages": [_tool_message(
                    runtime,
                    "检索完成。请根据以下澄清后的精确研究问题和已批准计划，"
                    "调用 select_papers 做唯一一次标题/摘要筛选。候选论文如下：\n\n"
                    f"精确研究问题：{state.get('clarified_query') or state.get('query', '')}\n"
                    f"计划范围：{(state.get('plan') or {}).get('title', '')}\n"
                    f"子问题：{'; '.join(state.get('sub_questions', []))}\n\n"
                    + _paper_preview(result.papers),
                )],
            }
        )
    finally:
        _SEARCH_IN_FLIGHT.discard(search_key)


def _tool_message(runtime: ToolRuntime, content: str) -> ToolMessage:
    return ToolMessage(content=content, tool_call_id=runtime.tool_call_id)


def _paper_preview(papers: Sequence[Mapping[str, Any]], *, limit: int = 20) -> str:
    """把标题和摘要交给主 Agent 做一次筛选，不暴露下载/分析内部字段。"""
    lines = []
    for index, paper in enumerate(papers[:limit], 1):
        lines.append(
            f"{index}. key={paper_key(paper)}\n"
            f"标题：{paper.get('title', '')}\n"
            f"年份：{paper.get('year', '')}\n"
            f"摘要：{str(paper.get('abstract') or '')[:1000]}"
        )
    return "\n\n".join(lines) or "（没有候选论文）"


def make_tools() -> list:
    """构造研究 tools。"""

    def _msg(runtime: ToolRuntime, content: str) -> ToolMessage:
        return ToolMessage(content=content, tool_call_id=runtime.tool_call_id)

    @tool
    async def search_papers(queries: list[str], runtime: ToolRuntime) -> Command:
        """通过 paper-search-mcp 统一检索并聚合论文。"""
        return await _search_command(queries, runtime)

    @tool
    async def download_papers(runtime: ToolRuntime) -> Command:
        """下载已检索到的论文 PDF 到本 session 专属目录。需先 search_papers。"""
        state = runtime.state
        if state.get("research_mode") == "quick":
            return Command(
                update={"messages": [_msg(runtime, "快速研究禁止下载论文，请直接调用 write_review。")]}
            )
        if state.get("research_mode") == "deep" and "selected_paper_keys" not in state:
            return Command(
                update={
                    "messages": [
                        _msg(runtime, "错误：深度研究需要先调用 select_papers 选择要深入分析的论文")
                    ]
                }
            )
        if not state.get("papers"):
            return Command(
                update={
                    "messages": [
                        _msg(runtime, "错误：还没检索到论文，请先调用 search_papers")
                    ]
                }
            )
        download_state: ResearchContext = {
            "papers": state["papers"],
            "session_id": state.get("session_id", ""),
        }
        if "selected_paper_keys" in state:
            download_state["selected_paper_keys"] = state["selected_paper_keys"]
        out = await downloader.run(download_state)
        ok = sum(
            1
            for p in out["papers"]
            if p.get("local_path") and p.get("download_status") != "not_selected"
        )
        fail = len(out.get("download_failures", []))
        body = f"下载完成：成功 {ok} 篇，失败 {fail} 篇。"
        body += ("请调用 analyze_papers 分析已下载论文，再调用 write_review；下载失败论文使用摘要。"
                 if ok else "请直接调用 write_review，使用下载失败论文的摘要，再调用 review_report 审校。")
        return Command(
            update={
                "papers": out["papers"],
                "download_failures": out.get("download_failures", []),
                "messages": [_msg(runtime, body)],
            }
        )

    @tool
    async def analyze_papers(runtime: ToolRuntime) -> Command:
        """解析已下载论文，抽取 problem/method/results/limitations/novelty。需先 download_papers。"""
        state = runtime.state
        if state.get("research_mode") == "quick":
            return Command(
                update={"messages": [_msg(runtime, "快速研究直接基于标题和摘要生成综述，无需深度解析")]}
            )
        analysis_papers = state.get("papers", [])
        if "selected_paper_keys" in state:
            selected = {str(key) for key in (state.get("selected_paper_keys") or [])}
            analysis_papers = [
                paper
                for paper in analysis_papers
                if bool(
                    {
                        paper_key(paper),
                        str(paper.get("paper_key") or ""),
                        str(paper.get("arxiv_id") or ""),
                    }
                    & selected
                )
            ]
        if not any(p.get("local_path") for p in analysis_papers):
            return Command(
                update={
                    "messages": [
                        _msg(runtime, "没有可下载分析的论文；若已下载失败，请调用 write_review 使用摘要，否则先调用 download_papers")
                    ]
                }
            )
        analysis_state: ResearchContext = {"papers": analysis_papers}
        if "selected_paper_keys" in state:
            analysis_state["selected_paper_keys"] = state["selected_paper_keys"]
        out = await analyzer.run(analysis_state)
        return Command(
            update={
                "analyses": out["analyses"],
                "messages": [
                    _msg(runtime, f"分析完成：{len(out['analyses'])} 篇提取了结构化信息")
                ],
            }
        )

    @tool
    async def write_review(
        runtime: ToolRuntime, instructions: str = ""
    ) -> Command:
        """基于论文/分析生成或改写综述草稿。

        Args:
            instructions: 修改指南（如"简化引言"/"扩写结论"/"翻译成英文"）。
                留空则按已有 ctx 生成初版；非空则在已有 draft 基础上改写（refine 模式）。
        """
        state = runtime.state
        if not state.get("papers"):
            return Command(
                update={
                    "messages": [
                        _msg(runtime, "错误：没有论文，请先调用 search_papers")
                    ]
                }
            )
        if "selected_paper_keys" not in state and state.get("research_mode") in {"quick", "deep"}:
            return Command(
                update={"messages": [_msg(runtime, "请先调用 select_papers 完成标题/摘要筛选。" )]}
            )
        selected = {str(key) for key in (state.get("selected_paper_keys") or [])}
        if not selected and "research_mode" not in state:
            selected = {paper_key(paper) for paper in state.get("papers", [])}
        selected_papers = [
            paper for paper in state.get("papers", [])
            if bool({paper_key(paper), str(paper.get("paper_key") or ""), str(paper.get("arxiv_id") or "")} & selected)
        ]
        if not selected_papers:
            return Command(
                update={"messages": [_msg(runtime, "没有通过筛选的论文，无法生成综述。" )]}
            )

        analyses = state.get("analyses", [])
        if state.get("research_mode") == "deep":
            selected_papers = synthesizer.evidence_papers(state)
            if not selected_papers:
                return Command(update={"messages": [_msg(
                    runtime, "没有可用证据：请先下载并分析；仅下载失败且有摘要的论文允许摘要回退。"
                )]})
            analyses = [a for p in selected_papers
                        if p.get("local_path") and (a := synthesizer.analysis_for(p, analyses))]
        # 用 instructions 作为本次 feedback；不修改 state.feedback（避免污染 review_report）
        synthesis_state: ResearchContext = {
            "query": state.get("clarified_query") or state.get("query", ""),
            "sub_questions": state.get("sub_questions", []),
            "plan": state.get("plan"),
            "research_mode": state.get("research_mode", "quick"),
            "papers": selected_papers,
            "analyses": analyses if state.get("research_mode") == "deep" else [],
            "feedback": instructions or state.get("feedback", ""),
            "draft": state.get("draft", ""),
            "iteration": state.get("iteration", 0),
            "download_failures": state.get("download_failures", []),
        }
        result = await synthesizer.run(synthesis_state)
        draft_text = result.get("draft", "")
        iteration = result.get("iteration", 0)
        action = "改写" if instructions else "生成"

        # M5.5.11: 附论文下载列表 + draft 首段 —— agent 追问时能搜到具体论文名
        papers = selected_papers
        downloaded = [p for p in papers if p.get("local_path")]
        failed = [p for p in papers if not p.get("local_path")]
        papers_info = (
            f"\n✅ 已下载（{len(downloaded)} 篇）：\n"
            + "\n".join(f"- [{citation_id(p)}] {p['title']}" for p in downloaded)
        ) if downloaded else ""
        if failed:
            papers_info += (
                f"\n\n❌ 未下载（{len(failed)} 篇，需自行查阅）：\n"
                + "\n".join(f"- [{citation_id(p)}] {p['title']}" for p in failed)
            )
        preview = draft_text[:500] + ("..." if len(draft_text) > 500 else "")
        body = (
            f"综述已{action}（{len(draft_text)} 字，第 {iteration} 版）。\n\n"
            f"{papers_info}\n\n"
            f"【综述正文前段】\n{preview}"
        )
        return Command(
            update={
                **result,  # draft + iteration
                "feedback": "",
                "messages": [_msg(runtime, body)],
            }
        )

    @tool
    async def review_report(runtime: ToolRuntime) -> Command:
        """审校当前综述草稿，返回是否通过及问题。需先 write_review。"""
        state = runtime.state
        if state.get("research_mode") == "quick":
            return Command(
                update={"messages": [_msg(runtime, "快速研究不执行综述审校。") ]}
            )
        if not state.get("draft"):
            return Command(
                update={
                    "messages": [
                        _msg(runtime, "错误：还没有综述草稿，请先调用 write_review")
                    ]
                }
            )
        from app.agents.nodes import reviewer

        report_papers = synthesizer.evidence_papers(state)
        out = await reviewer.run(
            {
                "papers": report_papers,
                "analyses": state.get("analyses", []),
                "research_mode": state.get("research_mode", "quick"),
                "sub_questions": state.get("sub_questions", []),
                "draft": state["draft"],
            }
        )
        if out.get("passed"):
            return Command(
                update={
                    "review_status": "passed",
                    "messages": [
                        _msg(
                            runtime,
                            "审校通过：综述覆盖完整、引用无误，可以结束了。",
                        )
                    ]
                }
            )
        cap = (
            "（已达改写上限，请结束）"
            if state.get("iteration", 0) >= 2
            else "请调用 write_review 改写。"
        )
        return Command(
            update={
                "review_status": "failed" if out.get("status") == "failed" else "pending",
                "feedback": out.get("feedback", ""),
                "messages": [_msg(runtime, f"审校未通过：{out.get('feedback','')}。{cap}")],
            }
        )

    @tool
    async def select_papers(paper_keys: list[str], runtime: ToolRuntime) -> Command:
        """从检索候选中选择值得下载和深度分析的论文。需先 search_papers。"""
        state = runtime.state
        papers = state.get("papers", [])
        if state.get("selected_paper_keys"):
            return Command(
                update={
                    "messages": [
                        _msg(
                            runtime,
                            f"已选择 {len(state['selected_paper_keys'])} 篇论文，请继续调用 download_papers。",
                        )
                    ]
                }
            )
        available: dict[str, dict] = {}
        for paper in papers:
            keys = {
                str(paper.get("paper_key") or ""),
                str(paper.get("arxiv_id") or ""),
                citation_id(paper),
                paper_key(paper),
            }
            for key in keys:
                if key:
                    available[key] = paper
        selected: list[str] = []
        invalid: list[str] = []
        for requested in paper_keys:
            value = str(requested).strip()
            paper = available.get(value)
            if paper is None:
                invalid.append(value)
                continue
            canonical = paper_key(paper)
            if canonical not in selected:
                selected.append(canonical)
        cap = int(state.get("max_papers", 20))
        selected = selected[:cap]
        if not selected:
            # Models occasionally return a title fragment or an empty list
            # instead of the canonical key. Keep the workflow moving by using
            # the ranked candidates as a deterministic fallback selection.
            selected = [paper_key(paper) for paper in papers[:cap]]
            if not selected:
                return Command(
                    update={
                        "messages": [
                            _msg(runtime, f"没有可用的论文选择。无效标识：{', '.join(invalid) or '无'}")
                        ]
                    }
                )
            detail = f"未识别有效标识，已按检索排序自动选择 {len(selected)} 篇论文进入下载和深度分析"
        else:
            detail = f"已选择 {len(selected)} 篇论文进入下载和深度分析"
        if invalid:
            detail += f"；忽略 {len(invalid)} 个无效标识"
        chosen = [paper for paper in papers if paper_key(paper) in set(selected)]
        return Command(update={
            "selected_paper_keys": selected,
            "messages": [_msg(runtime, detail + "。后续只能使用这些论文：\n\n" + _paper_preview(chosen))],
        })

    # Keep the first five positions stable for existing callers.  The selector
    # and old search alias are appended as optional tools.
    return [search_papers, download_papers, analyze_papers, write_review, review_report, select_papers]
