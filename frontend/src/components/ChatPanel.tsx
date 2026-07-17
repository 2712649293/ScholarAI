import { useEffect, useState, type FormEvent } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  chatQA,
  getSession,
  listKBs,
  researchStream,
  ApiError,
  type Citation,
  type KB,
  type ResearchFinal,
} from '@/lib/api'
import { ResearchProgress, labelForTool } from '@/components/ResearchProgress'

type Mode = 'qa' | 'research'

interface Message {
  role: 'user' | 'assistant'
  content: string
  citations?: Citation[]
  markdown?: boolean // 研究综述 → markdown 渲染 + 下载
}

function downloadMd(content: string) {
  const blob = new Blob([content], { type: 'text/markdown;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = 'report.md'
  a.click()
  URL.revokeObjectURL(url)
}

export function ChatPanel() {
  const { sessionId: routeSessionId } = useParams<{ sessionId?: string }>()
  const navigate = useNavigate()
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [sessionId, setSessionId] = useState<string | undefined>(routeSessionId)
  const [mode, setMode] = useState<Mode>('qa')
  const [researchSteps, setResearchSteps] = useState<string[]>([])

  const [kbs, setKBs] = useState<KB[]>([])
  const [selectedKBs, setSelectedKBs] = useState<Set<string>>(new Set())

  useEffect(() => {
    listKBs().then(setKBs).catch(() => setKBs([]))
  }, [])

  // M2.6.6: 路由 session 变化时加载历史（点侧边栏 / 直接开 URL / 新对话）
  useEffect(() => {
    if (!routeSessionId) {
      setSessionId(undefined)
      setMessages([])
      return
    }
    if (routeSessionId === sessionId) return // 自己刚创建的，别重复拉
    setSessionId(routeSessionId)
    getSession(routeSessionId)
      .then((s) => {
        if (s.mode === 'research') setMode('research')
        setMessages(
          s.messages
            .filter((m) => m.role === 'user' || m.role === 'assistant')
            .map((m) => ({
              role: m.role as 'user' | 'assistant',
              content: m.content,
              markdown: s.mode === 'research' && m.role === 'assistant',
            })),
        )
      })
      .catch(() => setMessages([]))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [routeSessionId])

  function toggleKB(id: string) {
    setSelectedKBs((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const canSend = input.trim().length > 0 && !loading

  function runResearch(query: string) {
    setResearchSteps([])
    researchStream({ query, session_id: sessionId, depth: 'normal' }, (event, data) => {
      if (event === 'step') {
        const { node } = data as { node: string }
        setResearchSteps((prev) => [...prev, labelForTool(node)])
      } else if (event === 'final') {
        const f = data as ResearchFinal
        setSessionId(f.session_id)
        setMessages((m) => [...m, { role: 'assistant', content: f.report_markdown, markdown: true }])
        setLoading(false)
        if (!routeSessionId) navigate(`/chat/${f.session_id}`)
      } else if (event === 'error') {
        setError((data as { message?: string; code?: string }).message ?? '研究失败')
        setLoading(false)
      }
    })
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (!canSend) return
    const query = input.trim()
    setMessages((m) => [...m, { role: 'user', content: query }])
    setInput('')
    setLoading(true)
    setError(null)

    if (mode === 'research') {
      runResearch(query)
      return
    }

    try {
      const { reply, session_id, citations } = await chatQA(query, sessionId, Array.from(selectedKBs))
      setSessionId(session_id)
      setMessages((m) => [...m, { role: 'assistant', content: reply, citations }])
      if (!routeSessionId) navigate(`/chat/${session_id}`)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex h-full flex-col">
      {/* 顶部栏：模式切换 + （问答模式）KB 选择 */}
      <div className="border-b border-zinc-200 bg-white px-4 py-2 text-xs">
        <div className="mx-auto flex max-w-3xl items-center gap-3">
          <div className="inline-flex overflow-hidden rounded-md border border-zinc-300">
            {(['qa', 'research'] as Mode[]).map((m) => (
              <button
                key={m}
                onClick={() => setMode(m)}
                disabled={loading}
                className={`px-3 py-1 ${
                  mode === m ? 'bg-blue-500 text-white' : 'bg-white text-zinc-600 hover:bg-zinc-50'
                } disabled:opacity-50`}
              >
                {m === 'qa' ? '问答模式' : '研究模式'}
              </button>
            ))}
          </div>
          {mode === 'qa' && (
            <div className="min-w-0 flex-1">
              <span className="text-zinc-500">知识库：</span>
              {kbs.length === 0 ? (
                <span className="text-zinc-400">（无，去知识库页创建）</span>
              ) : (
                <span className="space-x-2">
                  {kbs.map((kb) => (
                    <label key={kb.id} className="inline-flex cursor-pointer items-center gap-1">
                      <input
                        type="checkbox"
                        checked={selectedKBs.has(kb.id)}
                        onChange={() => toggleKB(kb.id)}
                        className="h-3 w-3"
                      />
                      <span className="text-zinc-700">{kb.name}</span>
                    </label>
                  ))}
                </span>
              )}
            </div>
          )}
          {mode === 'research' && (
            <span className="text-zinc-400">给个研究方向，自动检索 arxiv 生成综述</span>
          )}
        </div>
      </div>

      {/* 消息区 */}
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-3xl space-y-3 p-4">
          {messages.length === 0 && (
            <p className="pt-8 text-center text-sm text-zinc-400">
              {mode === 'qa' ? '开始对话吧' : '输入研究方向，例如「LLM 推理加速」'}
            </p>
          )}
          {messages.map((m, i) => (
            <MessageBubble key={i} msg={m} />
          ))}
          {loading && mode === 'research' && <ResearchProgress steps={researchSteps} />}
          {loading && mode === 'qa' && (
            <div className="mr-auto max-w-[80%] rounded-lg bg-white px-4 py-2 text-sm text-zinc-400 shadow-sm">
              思考中…
            </div>
          )}
          {error && (
            <div className="rounded-lg bg-red-50 px-4 py-2 text-sm text-red-600">错误：{error}</div>
          )}
        </div>
      </div>

      {/* 输入区 */}
      <form onSubmit={handleSubmit} className="border-t border-zinc-200 bg-white p-4">
        <div className="mx-auto flex max-w-3xl gap-2">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder={mode === 'qa' ? '输入你的问题…' : '输入研究方向…'}
            disabled={loading}
            className="flex-1 rounded-md border border-zinc-300 px-3 py-2 text-sm outline-none focus:border-blue-400 disabled:bg-zinc-100"
          />
          <button
            type="submit"
            disabled={!canSend}
            className="rounded-md bg-blue-500 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-600 disabled:cursor-not-allowed disabled:bg-zinc-300"
          >
            发送
          </button>
        </div>
      </form>
    </div>
  )
}

function MessageBubble({ msg }: { msg: Message }) {
  if (msg.role === 'user') {
    return (
      <div className="ml-auto max-w-[80%] rounded-lg bg-blue-500 px-4 py-2 text-sm text-white">
        {msg.content}
      </div>
    )
  }
  if (msg.markdown) {
    return (
      <div className="mr-auto w-full space-y-2">
        <div className="prose prose-sm max-w-none rounded-lg bg-white px-4 py-3 text-zinc-800 shadow-sm">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{msg.content}</ReactMarkdown>
        </div>
        <button
          onClick={() => downloadMd(msg.content)}
          className="rounded border border-zinc-300 px-2 py-0.5 text-xs text-zinc-600 hover:bg-zinc-50"
        >
          下载 .md
        </button>
      </div>
    )
  }
  return (
    <div className="mr-auto max-w-[80%] space-y-2">
      <div className="rounded-lg bg-white px-4 py-2 text-sm text-zinc-800 shadow-sm">
        {msg.content}
      </div>
      {msg.citations && msg.citations.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {msg.citations.map((c, i) => (
            <span
              key={i}
              title={c.text}
              className="cursor-default rounded bg-zinc-100 px-2 py-0.5 text-xs text-zinc-600"
            >
              [{i + 1}] {c.doc_id.slice(0, 8)} p.{c.page}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}
