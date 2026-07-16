import { useState, type FormEvent } from 'react'
import { chatQA, ApiError } from '@/lib/api'

interface Message {
  role: 'user' | 'assistant'
  content: string
}

export function ChatPanel() {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [sessionId, setSessionId] = useState<string | undefined>(undefined)

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
      const { reply, session_id } = await chatQA(userMsg.content, sessionId)
      setSessionId(session_id)
      setMessages((m) => [...m, { role: 'assistant', content: reply }])
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : String(err)
      setError(msg)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="mx-auto flex h-full max-w-3xl flex-col p-4">
      <div className="flex-1 space-y-3 overflow-y-auto pb-4">
        {messages.length === 0 && (
          <p className="text-center text-sm text-zinc-400">开始对话吧</p>
        )}
        {messages.map((m, i) => (
          <div
            key={i}
            className={
              m.role === 'user'
                ? 'ml-auto max-w-[80%] rounded-lg bg-blue-500 px-4 py-2 text-sm text-white'
                : 'mr-auto max-w-[80%] rounded-lg bg-white px-4 py-2 text-sm text-zinc-800 shadow-sm'
            }
          >
            {m.content}
          </div>
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

      <form onSubmit={handleSubmit} className="flex gap-2 border-t border-zinc-200 pt-3">
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
      </form>
    </div>
  )
}
