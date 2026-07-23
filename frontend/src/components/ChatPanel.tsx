import { useEffect, useRef, useState, type FormEvent } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  approvePlanStream,
  chatQA,
  continueResearchStream,
  getSession,
  listKBs,
  researchStream,
  rejectPlan,
  startPlan,
  updatePlan,
  ApiError,
  type Citation,
  type KB,
  type ResearchFinal,
  type ResearchPlan,
} from '@/lib/api'
import { ResearchProgress, labelForTool } from '@/components/ResearchProgress'
import { PlanCard } from '@/components/PlanCard'

type Mode = 'qa' | 'research'
type Phase = 'idle' | 'planning' | 'executing'

interface BaseMsg {
  role: 'user' | 'assistant'
  content: string
  citations?: Citation[]
  markdown?: boolean
}
interface PlanMsg {
  role: 'plan'
  plan: ResearchPlan
  status: 'pending' | 'edited' | 'approved' | 'rejected'
}
type Message = BaseMsg | PlanMsg

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
  const [phase, setPhase] = useState<Phase>('idle')
  const [error, setError] = useState<string | null>(null)
  const [sessionId, setSessionId] = useState<string | undefined>(routeSessionId)
  const [mode, setMode] = useState<Mode>('qa')
  const [researchSteps, setResearchSteps] = useState<string[]>([])

  const [kbs, setKBs] = useState<KB[]>([])
  const [selectedKBs, setSelectedKBs] = useState<Set<string>>(new Set())

  // M5.5.6: 已 approved plan 后再发方向 → 走 runFollowup（跳 planner，直接进 researcher）。
  // ponytail：useState 异步批处理 + React 闭包拿快照——handleSubmit 在 setState 后
  // 同步读时可能拿到旧值。用 ref 同步存，state 留作渲染用。
  const [hasApprovedPlan, setHasApprovedPlan] = useState(false)
  const hasApprovedPlanRef = useRef(false)

  // M_bug_sse_isolation + M5.5: 一套 sseAbortRef/sseRunIdRef 管 plan 和 execute 两条 SSE 流。
  // - abortRef() 取消 fetch（防后端继续推 chunk）
  // - runId 闭包校验（防 abort 已发但客户端 reader 残留 event）
  const sseAbortRef = useRef<(() => void) | null>(null)
  const sseRunIdRef = useRef<number>(0)

  useEffect(() => {
    listKBs().then(setKBs).catch(() => setKBs([]))
  }, [])

  // 路由切 session：abort 任何 in-flight 流 + runId 自增。
  useEffect(() => {
    return () => {
      sseAbortRef.current?.()
      sseAbortRef.current = null
      sseRunIdRef.current++
    }
  }, [routeSessionId])

  // 路由 session 变化时加载历史（侧边栏切换 / 新对话 / 直接打开 URL）
  useEffect(() => {
    if (!routeSessionId) {
      setSessionId(undefined)
      setMessages([])
      setResearchSteps([])
      setError(null)
      setPhase('idle')
      setHasApprovedPlan(false)
      hasApprovedPlanRef.current = false
      return
    }
    if (routeSessionId === sessionId) return
    setSessionId(routeSessionId)
    getSession(routeSessionId)
      .then((s) => {
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
        setResearchSteps([])
        setError(null)
        setPhase('idle')
        // ponytail：不在这里 reset hasApprovedPlan——只让切到"空 session"分支 reset。
        // 中途 setSessionId 触发的 re-render 不应清掉 approved 状态（否则追问路径断）。
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

  const canSend = input.trim().length > 0 && phase === 'idle'

  /** M5.5: 启动 plan 流程。
   *  - 调 POST /api/research/plan（一次性 fetch，< 5s）
   *  - 成功 → plan 消息入流，phase=idle 让用户能继续打字
   *  - 失败 → 错误提示，phase=idle
   */
  async function runPlan(query: string) {
    setPhase('planning')
    setError(null)
    setHasApprovedPlan(false) // 新 plan 流程开始，旧 approved 状态清掉
    hasApprovedPlanRef.current = false
    try {
      const resp = await startPlan({ query, session_id: sessionId })
      setSessionId(resp.session_id)
      setMessages((m) => [
        ...m,
        { role: 'plan', plan: resp.plan!, status: resp.plan_status as PlanMsg['status'] },
      ])
      if (!routeSessionId) navigate(`/chat/${resp.session_id}`)
      setPhase('idle')
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err))
      setPhase('idle')
    }
  }

  /** M5.5.6: 追问 — 已 approved plan 后再发方向，跳过 planner，调 /continue/stream。
   *  与 runExecute 几乎一样，只是 endpoint 不同、不再 map plan 状态、不传 edited_plan。
   */
  function runFollowup(query: string) {
    if (!sessionId) return
    setPhase('executing')
    setResearchSteps([])
    const runId = ++sseRunIdRef.current
    sseAbortRef.current?.()
    sseAbortRef.current = continueResearchStream(
      sessionId,
      query,
      (event, data) => {
        if (runId !== sseRunIdRef.current) return
        if (event === 'step') {
          const { node } = data as { node: string }
          setResearchSteps((prev) => [...prev, labelForTool(node)])
        } else if (event === 'final') {
          const f = data as ResearchFinal
          // 追加新一条 assistant markdown（不替换旧 draft —— 每轮对话独立）
          setMessages((m) => [
            ...m,
            { role: 'assistant', content: f.report_markdown, markdown: true },
          ])
          setPhase('idle')
          // M5.5.10: 追问失败原因提示
          if (f.failure_reason === 'search_failed') {
            setError('⚠️ 论文检索失败（arXiv 服务暂时不可达）。请稍后重试或编辑 plan 修改搜索词。')
          } else if (f.failure_reason === 'download_failed') {
            setError('⚠️ 论文 PDF 下载失败（arXiv 服务暂时不可达）。综述已基于摘要生成，过段时间可重试。')
          }
        } else if (event === 'error') {
          setError((data as { message?: string }).message ?? '执行失败')
          setPhase('idle')
        }
      },
    )
  }

  /** M5.5: 批准 plan → 走 SSE 执行流。
   *  - POST /api/research/{sid}/plan/approve（带可选 edited_plan）
   *  - SSE 推 step + final
   *  - final → 综述 markdown 入流
   */
  function runExecute(planMsgIndex: number, editedPlan?: ResearchPlan) {
    if (!sessionId) return
    setPhase('executing')
    setResearchSteps([])
    // M5.5.7: 批准瞬间立即把 plan.status 改 approved——按钮区立刻 readonly，
    // 防止 researcher 跑期间用户重复点编辑/拒绝。
    // error handler 失败时回滚到 pending。
    setMessages((m) =>
      m.map((msg, i) =>
        i === planMsgIndex && msg.role === 'plan'
          ? { ...msg, status: 'approved' as const }
          : msg,
      ),
    )
    const runId = ++sseRunIdRef.current
    sseAbortRef.current?.()
    sseAbortRef.current = approvePlanStream(
      sessionId,
      editedPlan ?? null,
      (event, data) => {
        if (runId !== sseRunIdRef.current) return
        if (event === 'step') {
          const { node } = data as { node: string }
          setResearchSteps((prev) => [...prev, labelForTool(node)])
        } else if (event === 'final') {
          const f = data as ResearchFinal
          // plan.status 已在函数开头设为 approved（幂等保留）
          setMessages((m) => [
            ...m,
            { role: 'assistant', content: f.report_markdown, markdown: true },
          ])
          setPhase('idle')
          // M5.5.6: approve 完成后进入"可追问"状态
          setHasApprovedPlan(true)
          hasApprovedPlanRef.current = true
          // M5.5.10: 失败原因提示
          if (f.failure_reason === 'search_failed') {
            setError('⚠️ 论文检索失败（arXiv 服务暂时不可达）。请稍后重试或编辑 plan 修改搜索词。')
          } else if (f.failure_reason === 'download_failed') {
            setError('⚠️ 论文 PDF 下载失败（arXiv 服务暂时不可达）。综述已基于摘要生成，过段时间可重试。')
          }
        } else if (event === 'error') {
          // 回滚：plan 回到 pending 让用户能重新编辑/拒绝/批准
          setMessages((m) =>
            m.map((msg, i) =>
              i === planMsgIndex && msg.role === 'plan'
                ? { ...msg, status: 'pending' as const }
                : msg,
            ),
          )
          setHasApprovedPlan(false)
          hasApprovedPlanRef.current = false
          setError((data as { message?: string }).message ?? '执行失败')
          setPhase('idle')
        }
      },
    )
  }

  /** 用户点 "拒绝" → 调 reject endpoint，卡片变灰。 */
  async function handleReject(planMsgIndex: number) {
    if (!sessionId) return
    try {
      await rejectPlan(sessionId)
      setMessages((m) =>
        m.map((msg, i) =>
          i === planMsgIndex && msg.role === 'plan'
            ? { ...msg, status: 'rejected' as const }
            : msg,
        ),
      )
      // M5.5.6: rejected 后再发方向 → 回 runPlan 路径重新生成 plan
      setHasApprovedPlan(false)
      hasApprovedPlanRef.current = false
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err))
    }
  }

  /** 用户点 "保存编辑" → PATCH 后状态变 edited（仍 pending 行为）。 */
  async function handleSaveEdit(planMsgIndex: number, patch: Partial<ResearchPlan>) {
    if (!sessionId) return
    try {
      await updatePlan(sessionId, patch)
      // 用 patch 更新本地 plan 消息
      setMessages((m) =>
        m.map((msg, i) => {
          if (i !== planMsgIndex || msg.role !== 'plan') return msg
          const merged = { ...msg.plan, ...patch }
          return { ...msg, plan: merged, status: 'edited' as const }
        }),
      )
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err))
    }
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (!canSend) return
    const query = input.trim()
    setMessages((m) => [...m, { role: 'user', content: query }])
    setInput('')
    setError(null)

    if (mode === 'research') {
      if (hasApprovedPlanRef.current) {
        runFollowup(query)
      } else {
        runPlan(query)
      }
      return
    }

    // QA 模式
    setPhase('planning') // 借用 phase='planning' 显示"思考中…"
    try {
      const { reply, session_id, citations } = await chatQA(query, sessionId, Array.from(selectedKBs))
      setSessionId(session_id)
      setMessages((m) => [...m, { role: 'assistant', content: reply, citations }])
      if (!routeSessionId) navigate(`/chat/${session_id}`)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err))
    } finally {
      setPhase('idle')
    }
  }

  return (
    <div className="flex h-full flex-col">
      {/* 顶部栏：模式切换 + （问答模式）KB 选择 */}
      <div className="border-b border-zinc-200 bg-white px-4 py-2 text-xs">
        <div className="mx-auto flex max-w-3xl items-center gap-3">
          <div className="inline-flex overflow-hidden rounded-md border border-zinc-300">
            {(['qa', 'research'] as Mode[]).map((m) => {
              const isLocked = messages.length > 0
              const isOtherMode = mode !== m
              const disabled = phase !== 'idle' || (isLocked && isOtherMode)
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
            <span className="text-zinc-400">给个研究方向，自动生成研究计划等你审</span>
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
          {messages.map((m, i) => {
            if (m.role === 'plan') {
              return (
                <PlanCard
                  key={i}
                  plan={m.plan}
                  status={m.status}
                  onApprove={() => runExecute(i)}
                  onReject={() => handleReject(i)}
                  onSave={(patch) => handleSaveEdit(i, patch)}
                />
              )
            }
            return <MessageBubble key={i} msg={m} />
          })}
          {phase === 'planning' && mode === 'research' && (
            <div className="mr-auto max-w-[80%] rounded-lg bg-white px-4 py-2 text-sm text-zinc-400 shadow-sm">
              正在生成研究计划…
            </div>
          )}
          {phase === 'executing' && <ResearchProgress steps={researchSteps} />}
          {phase === 'planning' && mode === 'qa' && (
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
            disabled={phase !== 'idle'}
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

function MessageBubble({ msg }: { msg: BaseMsg }) {
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