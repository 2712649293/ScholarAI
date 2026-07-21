import { useEffect, useRef, useState, type FormEvent } from 'react'
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

  // M_bug_sse_isolation: 跨 session 隔离 research SSE。
  // runId = 当前 in-flight 流的标识（与启动时的 sessionId 绑定），切换 session 时
  // 旧流的回调里 runId 已不匹配 → 丢弃事件 + abort fetch。两条防线：
  // 1) abortRef() 取消已发出请求（防后端继续推 SSE chunk）
  // 2) runId 闭包校验（防 abort 已发出但客户端解析层还有残留 event 进来）
  const researchAbortRef = useRef<(() => void) | null>(null)
  const researchRunIdRef = useRef<number>(0)

  useEffect(() => {
    listKBs().then(setKBs).catch(() => setKBs([]))
  }, [])

  // M_bug_sse_isolation: 路由切到新 session 前，强制 abort 旧 in-flight SSE。
  // 不 abort 的话：B 的研究流推 step/final 事件 → 旧闭包里的 setResearchSteps/setMessages/
  // setLoading 把 A 的 state 搅乱 + navigate 把 URL 抢回 B。
  useEffect(() => {
    return () => {
      researchAbortRef.current?.()
      researchAbortRef.current = null
      // 让任何尚在 in-flight 的回调都被 token 校验拦下
      researchRunIdRef.current++
    }
  }, [routeSessionId])

  // M2.6.6: 路由 session 变化时加载历史（点侧边栏 / 直接开 URL / 新对话）
  useEffect(() => {
    if (!routeSessionId) {
      setSessionId(undefined)
      setMessages([])
      setResearchSteps([])
      setError(null)
      setLoading(false)
      return
    }
    if (routeSessionId === sessionId) return // 自己刚创建的，别重复拉
    setSessionId(routeSessionId)
    getSession(routeSessionId)
      .then((s) => {
        // M_bug_fix: 无条件按 session.mode 设 mode（之前只 setMode('research') 导致
        // 切到 qa session 时 local mode 仍残留 research）。
        // 后端 get_or_create 在 session 创建时锁定 mode，已存在 session 不会更新——
        // 所以 s.mode 就是该 session 的"出身模式"，按它设 local state 即可。
        setMode(s.mode as Mode)
        setMessages(
          s.messages
            .filter((m) => m.role === 'user' || m.role === 'assistant')
            .map((m) => ({
              role: m.role as 'user' | 'assistant',
              content: m.content,
              markdown: s.mode === 'research' && m.role === 'assistant',
            })),
        )
        // 切 session 时清掉上一个 session 的 loading/researchSteps/error，
        // 否则用户从在跑的 B 切回 A 会看到 B 的「研究步骤」+ 「加载中」。
        setResearchSteps([])
        setError(null)
        setLoading(false)
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
    // 起一个新 runId；任何旧回调里 runId 不匹配都直接丢弃（防止 abort 已发但
    // 客户端 reader 还在解析残留 chunk 时把旧 event 写进新 session 的 state）。
    const runId = ++researchRunIdRef.current
    researchAbortRef.current?.() // 先取消上一次的 in-flight（如果有）
    researchAbortRef.current = researchStream(
      { query, session_id: sessionId, depth: 'normal' },
      (event, data) => {
        if (runId !== researchRunIdRef.current) return // stale callback，跳过
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
      },
    )
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
            {(['qa', 'research'] as Mode[]).map((m) => {
              // 模式锁定：已发过消息的 session 不能再切另一模式。
              // isLocked 来自 messages 长度——切到新 session 时 messages 被 useEffect 重置。
              const isLocked = messages.length > 0
              const isOtherMode = mode !== m
              const disabled = loading || (isLocked && isOtherMode)
              return (
                <button
                  key={m}
                  onClick={() => !disabled && setMode(m)}
                  disabled={disabled}
                  title={isLocked && isOtherMode ? '该 session 模式已锁定' : undefined}
                  className={`px-3 py-1 ${
                    mode === m ? 'bg-blue-500 text-white' : 'bg-white text-zinc-600'
                  } ${isLocked && isOtherMode ? 'cursor-not-allowed opacity-50' : 'hover:bg-zinc-50'}`}
                >
                  {m === 'qa' ? '问答模式' : '研究模式'}
                </button>
              )
            })}
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
