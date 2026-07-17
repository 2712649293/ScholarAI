const RESEARCH_STEPS = [
  { node: 'planner', label: '规划研究范围' },
  { node: 'searcher', label: '检索 arxiv' },
  { node: 'downloader', label: '下载 PDF' },
  { node: 'analyzer', label: '解析论文' },
  { node: 'synthesizer', label: '生成综述' },
  { node: 'reviewer', label: '审校' },
]

export function ResearchProgress({ done }: { done: Set<string> }) {
  return (
    <div className="space-y-1 rounded-lg bg-white px-4 py-3 text-sm shadow-sm">
      {RESEARCH_STEPS.map((s) => {
        const isDone = done.has(s.node)
        return (
          <div
            key={s.node}
            className={isDone ? 'text-green-600' : 'text-zinc-400'}
          >
            {isDone ? '✓' : '⏳'} {s.label}
          </div>
        )
      })}
    </div>
  )
}
