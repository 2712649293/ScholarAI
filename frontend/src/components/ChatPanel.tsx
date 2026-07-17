import { useEffect, useState, type FormEvent } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { chatQA, getSession, listKBs, ApiError, type Citation, type KB } from '@/lib/api'

interface Message {
  role: 'user' | 'assistant'
  content: string
  citations?: Citation[]
}

export function ChatPanel() {
  const { sessionId: routeSessionId } = useParams<{ sessionId?: string }>()
  const navigate = useNavigate()
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [sessionId, setSessionId] = useState<string | undefined>(routeSessionId)

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
      .then((s) =>
        setMessages(
          s.messages
            .filter((m) => m.role === 'user' || m.role === 'assistant')
            .map((m) => ({ role: m.role as 'user' | 'assistant', content: m.content })),
        ),
      )
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

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (!canSend) return
    const userMsg: Message = { role: 'user', content: input.trim() }
    setMessages((m) => [...m, userMsg])
    setInput('')
    setLoading(true)
    setError(null)
    try {
      const { reply, session_id, citations } = await chatQA(
        userMsg.content,
        sessionId,
        Array.from(selectedKBs),
      )
      setSessionId(session_id)
      setMessages((m) => [...m, { role: 'assistant', content: reply, citations }])
      // 新对话：把 session id 反映到 URL，侧边栏才能高亮 + 刷新列表
      if (!routeSessionId) navigate(`/chat/${session_id}`)
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : String(err)
      setError(msg)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="flex h-full flex-col">
      {/* KB 选择条 */}
      <div className="border-b border-zinc-200 bg-white px-4 py-2 text-xs">
        <div className="mx-auto max-w-3xl">
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
      </div>

      {/* 消息区 */}
      <div className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-3xl space-y-3 p-4">
          {messages.length === 0 && (
            <p className="pt-8 text-center text-sm text-zinc-400">开始对话吧</p>
          )}
          {messages.map((m, i) => (
            <MessageBubble key={i} msg={m} />
          ))}
          {loading && (
            <div className="mr-auto max-w-[80%] rounded-lg bg-white px-4 py-2 text-sm text-zinc-400 shadow-sm">
              思考中…
            </div>
          )}
          {error && (
            <div className="rounded-lg bg-red-50 px-4 py-2 text-sm text-red-600">
              错误：{error}
            </div>
          )}
        </div>
      </div>

      {/* 输入区 */}
      <form
        onSubmit={handleSubmit}
        className="border-t border-zinc-200 bg-white p-4"
      >
        <div className="mx-auto flex max-w-3xl gap-2">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="输入你的问题…"
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
