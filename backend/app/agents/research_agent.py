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

SYSTEM_PROMPT = """你是学术研究主管 agent。目标：给定研究方向，产出一篇有 [arxiv_id] 引用的结构化中文综述。

可用工具：search_arxiv / download_papers / analyze_papers / write_review / review_report。

标准流程（可按情况调整，不必死板）：
1. search_arxiv：把研究方向拆成 3-5 个英文检索短语去搜。若累计 <5 篇，换关键词再搜一次。
2. download_papers：下载检索到的论文。
3. analyze_papers：解析已下载论文（下载失败的会自动跳过）。
4. write_review：生成综述草稿。
5. review_report：审校。未通过就按反馈再调用 write_review 改写，最多 2 版；通过或达上限即结束。

关键规则：
- **下载失败是常态**——arxiv PDF 在很多网络环境下不可达。若 download_papers 返回"成功 0 篇"或失败率高，**禁止再次调用 search_arxiv**，**直接调用 write_review** 用已有 abstract 生成综述（M2 降级路径已支持）。
- 综述质量不一定需要 PDF：abstract 完全可以支撑结构化综述。
- 工具有前置依赖。若某工具返回"错误：..."，按提示先补齐前置步骤，别重复报错的调用。
- 冷门方向可能搜不到论文，尝试 2 次仍为空就直接结束。
- 完成后用一句话说明即可，综述正文已由 write_review 保存，无需在回复里重复全文。"""


def build_agent(ctx: ResearchContext):
    """构造绑定到 ctx 的 ReAct agent。"""
    return create_agent(get_llm(), make_tools(ctx), system_prompt=SYSTEM_PROMPT)
