"""ReAct 研究主 agent（M4.5.1：langgraph 标准 + checkpointer）。

- state_schema = ResearchState（完整字段，checkpointer 持久化）
- checkpointer = AsyncSqliteSaver（必须用：create_agent 用 ainvoke 调 aput/aget）
- 多 session 隔离 = thread_id 不同 → checkpointer 物理分桶
- init_saver 是 **async**，必须在 running event loop 里调（lifespan / with TestClient）
"""
from __future__ import annotations

import threading
from pathlib import Path

import aiosqlite
from langchain.agents import create_agent
from langchain.agents.middleware import before_model
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Overwrite
from langgraph.runtime import Runtime

from app.agents.state import ResearchState
from app.agents.tools import make_tools
from app.config import settings
from app.llm import get_llm

# 硬闸：super-step 上限，防 agent 空转烧 token（每次 tool 调用约 2 步）
RECURSION_LIMIT = 25

# checkpointer DB 路径：与业务 DB 分离，关注点解耦
CHECKPOINTS_DB = Path(settings.database_url.replace("sqlite:///", "")).parent / "checkpoints.db"

# 单例 saver（lifespan 时初始化）
_saver: AsyncSqliteSaver | None = None
_conn: aiosqlite.Connection | None = None


async def init_saver() -> None:
    """FastAPI lifespan / 测试 setup 调用（async：必须 running event loop）。"""
    global _saver, _conn
    CHECKPOINTS_DB.parent.mkdir(parents=True, exist_ok=True)
    _conn = await aiosqlite.connect(str(CHECKPOINTS_DB))
    _saver = AsyncSqliteSaver(_conn)
    await _saver.setup()


async def close_saver() -> None:
    global _saver, _conn
    if _conn is not None:
        await _conn.close()
    _saver = None
    _conn = None


def get_saver() -> AsyncSqliteSaver:
    """M5.5：plan_graph 复用同一 saver（thread_id=session_id 共享 checkpointer）。

    调用前必须 init_saver()（lifespan / TestClient lifespan 触发）。
    """
    if _saver is None:
        raise RuntimeError("saver 未初始化：先在 lifespan 里调 init_saver()")
    return _saver


@before_model
def cap_papers(state: ResearchState, runtime: Runtime) -> dict | None:
    papers = state.get("papers", [])
    cap = state.get("max_papers", 20)
    if len(papers) > cap:
        return {"papers": Overwrite(papers[:cap])}
    return None


def build_agent() -> CompiledStateGraph:
    """构造带 checkpointer 的 ReAct agent。调用前必须 init_saver。"""
    if _saver is None:
        raise RuntimeError("saver 未初始化：先在 lifespan 里调 init_saver()")
    return create_agent(
        get_llm(),
        make_tools(),
        system_prompt=SYSTEM_PROMPT,
        state_schema=ResearchState,
        checkpointer=_saver,
        middleware=[cap_papers],
    )


SYSTEM_PROMPT = """你是学术研究助手。基于已有研究上下文（论文列表 / 综述草稿 / 子问题 / 完整消息历史），按需调工具回答用户。

可用工具：search_papers / select_papers / download_papers / analyze_papers / write_review / review_report。

**用户意图识别**（关键——不要默认全跑工具）：
- **新研究**：用户给方向、要求"再找几篇"、"换关键词搜"等 → 调 search_papers。search_papers 通过 paper-search-mcp 统一检索并由 MCP 服务负责来源聚合。检索返回标题和摘要后，必须调用 select_papers，按照澄清后的精确问题和已批准计划做唯一一次筛选。
- **快速研究**（state.research_mode=quick）：search_papers → select_papers → write_review。综述只基于筛选后的标题和摘要；禁止调用 download_papers、analyze_papers、review_report。
- **深度研究**（state.research_mode=deep）：search_papers → select_papers → download_papers → analyze_papers → write_review → review_report。优先使用成功下载论文的分析结果；下载失败的已筛选论文使用摘要并标明证据来源。全部下载失败时跳过 analyze_papers，仍需 write_review → review_report。
- **修改综述**：用户说"简化引言"/"扩写结论"/"翻译成英文"/"加一节..."等 → **直接调 write_review 并传 `instructions`**，会在已有 draft 上改写
- **问答/解释**：用户问"X 是什么"/"这段什么意思"等 → **不调任何工具**，用已有 state.papers / state.draft 直接回答
- **审校**：用户说"审校一下"/"自检"等 → 调 review_report

**规则**：
- **不必每次都调全套工具**——按当前需求选 1-2 个即可
- **下载失败是常态**：若 download_papers 返回"成功 0 篇"，**禁止再次 search_papers**，直接 write_review 使用已筛选论文的摘要，再 review_report；无摘要的失败论文不可作为证据
- 工具前置依赖：若返回"错误：..."，按提示先补齐前置，别重复错误调用
- 冷门方向搜 2 次仍空就结束
- 完成后一句话说明做了什么；综述全文由 write_review 保存，无需在回复里重复

**【M5.5.8 关键约束】如果 history 中出现"用户已批准以下研究计划"消息**：
- **必须严格按 plan.title 范围搜索 + 写综述**，不得偏离用户已批准的方向
- search_papers 的 queries **必须围绕 plan.search_queries 中的关键词**展开，不得引入 plan 之外无关概念
- 综述章节 **必须按 plan.outline 写**，不得擅自调整章节结构
- 若论文资料与 plan 方向不符（下载失败回退摘要本身不代表偏题），在综述开头明确标注"以下综述基于 X 篇论文，与计划方向『{plan.title}』存在偏差"，而非强行写偏题综述"""
