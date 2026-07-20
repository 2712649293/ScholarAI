"""研究子 agent = tool（M4.5.1）。

每个 tool 包一个现有 worker 节点的逻辑，读写 per-request ResearchContext，
只返回一句简短 observation（重数据留在 ctx，不进对话历史）。
半约束：前置校验在 tool 里，乱序调用返回"错误"让 agent 自纠。
"""
from __future__ import annotations

from langchain_core.tools import BaseTool, tool

from app.agents.context import ResearchContext
from app.agents.nodes import analyzer, downloader, reviewer, searcher, synthesizer


def make_tools(ctx: ResearchContext) -> list[BaseTool]:
    """构造绑定到 ctx 的 5 个 tool。每请求调一次。"""

    @tool
    async def search_arxiv(queries: list[str]) -> str:
        """按英文关键词短语列表检索 arxiv 论文（queries 建议 3-5 个短语）。可多次调用累积去重。"""
        ctx.search_queries = list(dict.fromkeys(ctx.search_queries + queries))
        out = await searcher.run(
            {"search_queries": queries, "depth": ctx.depth, "max_papers": ctx.max_papers}
        )
        by_id = {p["arxiv_id"]: p for p in ctx.papers}
        for p in out.get("papers", []):
            by_id.setdefault(p["arxiv_id"], p)
        ctx.papers = list(by_id.values())[: ctx.max_papers]
        return f"检索完成，当前累计 {len(ctx.papers)} 篇论文"

    @tool
    async def download_papers() -> str:
        """下载已检索到的论文 PDF 到本 session 专属目录。需先 search_arxiv。"""
        if not ctx.papers:
            return "错误：还没检索到论文，请先调用 search_arxiv"
        out = await downloader.run({"papers": ctx.papers, "session_id": ctx.session_id})
        ctx.papers = out["papers"]
        ctx.download_failures = out["download_failures"]
        ok = sum(1 for p in ctx.papers if p.get("local_path"))
        if ok == 0:  # 防死循环：全失败时强信号让 agent 退到 abstract，不要再 search
            return (
                f"下载完成：成功 0 篇，失败 {len(ctx.download_failures)} 篇（PDF 不可达或网络问题）。"
                f"**请直接调用 write_review 用已有 abstract 生成综述，**"
                f"**禁止再次调用 search_arxiv。**"
            )
        return f"下载完成：成功 {ok} 篇，失败 {len(ctx.download_failures)} 篇"

    @tool
    async def analyze_papers() -> str:
        """解析已下载论文，抽取 problem/method/results/limitations/novelty。需先 download_papers。"""
        if not any(p.get("local_path") for p in ctx.papers):
            return "错误：还没有已下载的论文，请先调用 download_papers"
        out = await analyzer.run({"papers": ctx.papers})
        ctx.analyses = out["analyses"]
        return f"分析完成：{len(ctx.analyses)} 篇提取了结构化信息"

    @tool
    async def write_review(instructions: str = "") -> str:
        """基于论文/分析生成或改写综述草稿。

        Args:
            instructions: 修改指南（如"简化引言"/"扩写结论"/"翻译成英文"）。
                留空则按已有 ctx 生成初版；非空则在已有 draft 基础上改写（refine 模式）。
        """
        if not ctx.papers:
            return "错误：没有论文，请先调用 search_arxiv"
        out = await synthesizer.run(
            {
                "query": ctx.query,
                "papers": ctx.papers,
                "analyses": ctx.analyses,
                "download_failures": ctx.download_failures,
                "sub_questions": ctx.sub_questions,
                # 用 instructions 作为本次 feedback；不修改 ctx.feedback（避免污染 review_report 路径）
                "feedback": instructions,
                "iteration": ctx.iteration,
                "draft": ctx.draft,  # 让 synthesizer 看到原 draft 以触发 refine 模式
            }
        )
        ctx.draft = out["report_draft"]
        ctx.iteration = out["iteration"]
        action = "改写" if instructions else "生成"
        return f"综述已{action}（{len(ctx.draft)} 字，第 {ctx.iteration} 版）"

    @tool
    async def review_report() -> str:
        """审校当前综述草稿，返回是否通过及问题。需先 write_review。"""
        if not ctx.draft:
            return "错误：还没有综述草稿，请先调用 write_review"
        out = await reviewer.run(
            {
                "papers": ctx.papers,
                "sub_questions": ctx.sub_questions,
                "report_draft": ctx.draft,
            }
        )
        ctx.feedback = out.get("feedback", "")
        if out.get("passed"):
            return "审校通过：综述覆盖完整、引用无误，可以结束了。"
        cap = "（已达改写上限，请结束）" if ctx.iteration >= 2 else "请调用 write_review 改写。"
        return f"审校未通过：{ctx.feedback}。{cap}"

    return [search_arxiv, download_papers, analyze_papers, write_review, review_report]
