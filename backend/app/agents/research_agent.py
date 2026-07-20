"""ReAct 研究主 agent（M4.5.2）。

主 agent 动态决定调哪个子 agent（tool）。半约束靠 prompt 规则 + tool 前置校验 +
recursion_limit（在 api 层 invoke 时传）。每请求新建 agent+context，避免并发串数据。
"""
from __future__ import annotations

from langchain.agents import create_agent

from app.agents.context import ResearchContext
from app.agents.tools import make_tools
from app.llm import get_llm

# 硬闸：super-step 上限，防 agent 空转烧 token（每次 tool 调用约 2 步）
RECURSION_LIMIT = 25

SYSTEM_PROMPT = """你是学术研究助手。基于已有研究上下文（论文列表 / 综述草稿 / 子问题），按需调工具回答用户。

可用工具：search_arxiv / download_papers / analyze_papers / write_review / review_report。

**用户意图识别**（关键——不要默认全跑工具）：
- **新研究**：用户给方向、要求"再找几篇"、"换关键词搜"等 → 调 search_arxiv（必要时 download + analyze）
- **修改综述**：用户说"简化引言"/"扩写结论"/"翻译成英文"/"加一节..."等 → **直接调 write_review 并传 `instructions`**，会在已有 draft 上改写
- **问答/解释**：用户问"X 是什么"/"这段什么意思"等 → **不调任何工具**，用 ctx.papers / ctx.draft 直接回答
- **审校**：用户说"审校一下"/"自检"等 → 调 review_report

**规则**：
- **不必每次都调全套工具**——按当前需求选 1-2 个即可
- **下载失败是常态**：若 download_papers 返回"成功 0 篇"，**禁止再次 search_arxiv**，直接 write_review 用 abstract 综述
- 工具前置依赖：若返回"错误：..."，按提示先补齐前置，别重复错误调用
- 冷门方向搜 2 次仍空就结束
- 完成后一句话说明做了什么；综述全文由 write_review 保存，无需在回复里重复"""


def build_agent(ctx: ResearchContext):
    """构造绑定到 ctx 的 ReAct agent。"""
    return create_agent(get_llm(), make_tools(ctx), system_prompt=SYSTEM_PROMPT)
