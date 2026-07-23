"""研究子 agent = tool（M4.5.1：langgraph 标准格式）。

每个 tool 接收 `ToolRuntime` 访问 state，**用 `Command(update=...)` 写回**。
不依赖闭包/ResearchContext，state 全部在 langgraph state（checkpointer 持久化）。
半约束：前置校验在 tool 里，乱序调用返回错误让 agent 自纠。
"""
from __future__ import annotations

from langchain.tools import tool, ToolRuntime
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from app.agents.nodes import analyzer, downloader, searcher, synthesizer
from app.errors import ArxivFetchFailed


def make_tools() -> list:
    """构造 5 个 tool，绑定 langgraph state。无闭包依赖。"""

    def _msg(runtime: ToolRuntime, content: str) -> ToolMessage:
        return ToolMessage(content=content, tool_call_id=runtime.tool_call_id)

    @tool
    async def search_arxiv(queries: list[str], runtime: ToolRuntime) -> Command:
        """按英文关键词短语列表检索 arxiv 论文（queries 建议 3-5 个短语）。可多次调用累积去重。"""
        state = runtime.state
        new_queries = list(dict.fromkeys(state.get("search_queries", []) + queries))
        try:
            out = await searcher.run(
                {
                    "search_queries": queries,
                    "depth": state.get("depth", "normal"),
                    "max_papers": state.get("max_papers", 20),
                }
            )
        except ArxivFetchFailed as e:
            # M5.5.9: arxiv 全部失败/超时 → 翻译成 ToolMessage，agent 看到后
            # 决定下一步（重试 / 改 query / 接受失败）。不再让异常冒泡中断 run。
            return Command(
                update={
                    "messages": [
                        _msg(
                            runtime,
                            (
                                f"arxiv 检索失败（{len(queries)} 个 query 全部超时或不可用）。"
                                f"建议：1) 等待几分钟后重试 2) 修改 search_queries（在 plan 卡片点编辑）"
                                f" 3) 接受失败结束"
                            ),
                        )
                    ]
                }
            )
        by_id = {p["arxiv_id"]: p for p in state.get("papers", [])}
        for p in out.get("papers", []):
            by_id.setdefault(p["arxiv_id"], p)
        max_papers = state.get("max_papers", 20)
        merged = list(by_id.values())[:max_papers]
        return Command(
            update={
                "search_queries": new_queries,
                "papers": merged,
                "messages": [_msg(runtime, f"检索完成，当前累计 {len(merged)} 篇论文")],
            }
        )

    @tool
    async def download_papers(runtime: ToolRuntime) -> Command:
        """下载已检索到的论文 PDF 到本 session 专属目录。需先 search_arxiv。"""
        state = runtime.state
        if not state.get("papers"):
            return Command(
                update={
                    "messages": [
                        _msg(runtime, "错误：还没检索到论文，请先调用 search_arxiv")
                    ]
                }
            )
        out = await downloader.run(
            {"papers": state["papers"], "session_id": state.get("session_id", "")}
        )
        ok = sum(1 for p in out["papers"] if p.get("local_path"))
        fail = len(out.get("download_failures", []))
        # 防死循环：全失败时强信号让 agent 退到 abstract，不要再 search
        body = (
            f"下载完成：成功 {ok} 篇，失败 {fail} 篇（PDF 不可达或网络问题）。"
            f"**请直接调用 write_review 用已有 abstract 生成综述，**"
            f"**禁止再次调用 search_arxiv。**"
        ) if ok == 0 else f"下载完成：成功 {ok} 篇，失败 {fail} 篇"
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
        if not any(p.get("local_path") for p in state.get("papers", [])):
            return Command(
                update={
                    "messages": [
                        _msg(runtime, "错误：还没有已下载的论文，请先调用 download_papers")
                    ]
                }
            )
        out = await analyzer.run({"papers": state["papers"]})
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
                        _msg(runtime, "错误：没有论文，请先调用 search_arxiv")
                    ]
                }
            )
        # 用 instructions 作为本次 feedback；不修改 state.feedback（避免污染 review_report）
        result = await synthesizer.run({**state, "feedback": instructions})
        action = "改写" if instructions else "生成"
        body = f"综述已{action}（{len(result.get('draft', ''))} 字，第 {result.get('iteration', 0)} 版）"
        return Command(
            update={
                **result,  # draft + iteration
                "messages": [_msg(runtime, body)],
            }
        )

    @tool
    async def review_report(runtime: ToolRuntime) -> Command:
        """审校当前综述草稿，返回是否通过及问题。需先 write_review。"""
        state = runtime.state
        if not state.get("draft"):
            return Command(
                update={
                    "messages": [
                        _msg(runtime, "错误：还没有综述草稿，请先调用 write_review")
                    ]
                }
            )
        from app.agents.nodes import reviewer

        out = await reviewer.run(
            {
                "papers": state.get("papers", []),
                "sub_questions": state.get("sub_questions", []),
                "report_draft": state["draft"],
            }
        )
        if out.get("passed"):
            return Command(
                update={
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
                "feedback": out.get("feedback", ""),
                "messages": [_msg(runtime, f"审校未通过：{out.get('feedback','')}。{cap}")],
            }
        )

    return [search_arxiv, download_papers, analyze_papers, write_review, review_report]
