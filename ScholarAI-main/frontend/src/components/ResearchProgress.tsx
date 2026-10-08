// tool 名 → 时间线显示的标签
const TOOL_LABELS: Record<string, string> = {
  search_arxiv: '🔍 检索 arxiv',
  download_papers: '⬇ 下载 PDF',
  analyze_papers: '🧠 解析论文',
  write_review: '✍ 生成综述',
  review_report: '✅ 审校',
}

export function labelForTool(node: string): string {
  return TOOL_LABELS[node] ?? `▶ ${node}`
}

/** 动态时间线：agent 每调一次 tool 追加一条（可重复/跳过）。 */
export function ResearchProgress({ steps }: { steps: string[] }) {
  return (
    <div className="space-y-1 rounded-lg bg-white px-4 py-3 text-sm shadow-sm">
      {steps.length === 0 ? (
        <div className="text-zinc-400">⏳ 主 agent 规划中…</div>
      ) : (
        steps.map((label, i) => (
          <div key={i} className="text-zinc-700">
            {label}
          </div>
        ))
      )}
    </div>
  )
}
