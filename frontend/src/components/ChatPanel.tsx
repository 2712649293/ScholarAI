import { useEffect, useRef, useState, type FormEvent } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  approvePlanStream,
  chatQA,
  cancelResearch,
  clarifyStream,
  clarifyContinueStream,
  continueResearchStream,
  researchEvents,
  resumeResearchStream,
  getSession,
  getPlan,
  listKBs,
  rejectPlan,
  startPlan,
  updatePlan,
  ApiError,
  type Citation,
  type ClarifyConfirmed,
  type KB,
  type ResearchFinal,
  type ResearchPlan,
} from '@/lib/api'
import { ResearchProgress, labelForTool } from '@/components/ResearchProgress'
import { PlanCard } from '@/components/PlanCard'

type Mode = 'qa' | 'research'
type Phase = 'idle' | 'clarifying' | 'planning' | 'executing'

interface BaseMsg {
  id?: string
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
  const [recovery, setRecovery] = useState<'resume' | 'reconnect' | null>(null)
  const [sessionId, setSessionId] = useState<string | undefined>(undefined)
  const [mode, setMode] = useState<Mode>('qa')
  const [researchSteps, setResearchSteps] = useState<string[]>([])

  const [kbs, setKBs] = useState<KB[]>([])
  const [selectedKBs, setSelectedKBs] = useState<Set<string>>(new Set())

  // M5.5.6: 已 approved plan 后再发方向 → 走 runFollowup（跳 planner，直接进 researcher）。
  // ponytail：useState 异步批处理 + React 闭包拿快照——handleSubmit 在 setState 后
  // 同步读时可能拿到旧值。用 ref 同步存，state 留作渲染用。
  const hasApprovedPlanRef = useRef(false)

  // M_bug_sse_isolation + M5.5: 一套 sseAbortRef/sseRunIdRef 管 plan 和 execute 两条 SSE 流。
  // - abortRef() 取消 fetch（防后端继续推 chunk）
  // - runId 闭包校验（防 abort 已发但客户端 reader 残留 event）
  const sseAbortRef = useRef<(() => void) | null>(null)
  const sseRunIdRef = useRef<number>(0)
  const clarifyActiveRef = useRef(false)

  useEffect(() => {
    listKBs().then(setKBs).catch(() => setKBs([]))
  }, [])

  // Detach the view on navigation; research execution now belongs to the backend.
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
      setRecovery(null)
      setPhase('idle')
      hasApprovedPlanRef.current = false
      clarifyActiveRef.current = false
      return
    }
    if (routeSessionId === sessionId) return
    let cancelled = false
    setSessionId(routeSessionId)
    setMessages([])
    setResearchSteps([])
    setError(null)
    setPhase('planning')
    setRecovery(null)
    hasApprovedPlanRef.current = false
    clarifyActiveRef.current = false
    ;(async () => {
      const session = await getSession(routeSessionId)
      const plan = session.mode === 'research' ? await getPlan(routeSessionId) : null
      if (cancelled) return
      setMode(session.mode === 'research' ? 'research' : 'qa')
      const history: Message[] = session.messages
        .filter((m) => m.role === 'user' || m.role === 'assistant')
        .map((m) => ({
          id: m.id, role: m.role as 'user' | 'assistant', content: m.content,
          markdown: session.mode === 'research' && m.role === 'assistant',
        }))
      if (plan?.plan && ['pending', 'edited', 'approved'].includes(plan.plan_status)) {
        history.push({ role: 'plan', plan: plan.plan, status: plan.plan_status as PlanMsg['status'] })
      }
      setMessages(history)
      const approved = plan?.plan_status === 'approved'
      hasApprovedPlanRef.current = approved
      clarifyActiveRef.current = plan?.plan_status === 'clarifying'
      if (plan?.execution_status === 'running' || (session.status === 'running' && plan?.execution_status === 'done')) {
        observeResearch(routeSessionId, (onEvent) => researchEvents(routeSessionId, onEvent))
      } else {
        setPhase('idle')
        setError(plan?.execution_error ?? null)
        setRecovery(plan?.can_resume ? 'resume' : null)
      }
    })().catch((err) => {
      if (!cancelled) {
        setError(err instanceof Error ? err.message : String(err))
        setPhase('idle')
      }
    })
    return () => { cancelled = true }
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

  const canSend = input.trim().length > 0 && phase === 'idle' && recovery === null


  /** M5.6: 启动/继续澄清对话。先于 plan 流程。
   * - first call: clarifyStream → SSE message/confirmed 事件
   * - subsequent: clarifyContinueStream → SSE message/confirmed 事件
   * - confirmed: auto call startPlan → plan 卡片入流
   */
  function runClarify(query: string, isContinue = false) {
    if (!sessionId && isContinue) return
    setPhase('clarifying')
    setError(null)
    clarifyActiveRef.current = true
    const body = isContinue ? null : { query, session_id: sessionId }
    const sseFn = isContinue
      ? (on: (e: string, d: unknown) => void) => clarifyContinueStream(sessionId!, query, on)
      : (on: (e: string, d: unknown) => void) => clarifyStream(body!, on)
    let streamSessionId = sessionId

    const runId = ++sseRunIdRef.current
    sseAbortRef.current?.()
    sseAbortRef.current = sseFn((event, data) => {
      if (runId !== sseRunIdRef.current) return
      if (event === 'session') {
        // 只记 sessionId，不 navigate——navigate 会触发 useEffect cleanup
        // 把正在跑的 SSE 流 abort 掉，导致后续 message 事件收不到。
        streamSessionId = (data as { session_id: string }).session_id
        setSessionId(streamSessionId)
      } else if (event === 'message') {
        const { content } = data as { content: string }
        setMessages((m) => [...m, { role: 'assistant', content }])
      } else if (event === 'step') {
        // agent called web_search — no visible change needed
      } else if (event === 'confirmed') {
        const cf = data as ClarifyConfirmed
        setSessionId(cf.session_id)
        clarifyActiveRef.current = false
        setPhase('planning')
        // auto-trigger plan generation
        startPlan({
          query: cf.clarify_direction.refined_query,
          session_id: cf.session_id,
          research_mode: cf.research_mode ?? 'quick',
        })
          .then((resp) => {
            if (runId !== sseRunIdRef.current) return
            setMessages((m) => [
              ...m,
              { role: 'plan', plan: resp.plan!, status: resp.plan_status as PlanMsg['status'] },
            ])
            if (!routeSessionId) navigate(`/chat/${resp.session_id}`)
            setPhase('idle')
          })
          .catch((err) => {
            if (runId !== sseRunIdRef.current) return
            setError(err instanceof ApiError ? err.message : String(err))
            setPhase('idle')
          })
      } else if (event === 'complete') {
        if (clarifyActiveRef.current) setPhase('idle')
        if (!routeSessionId && streamSessionId) navigate(`/chat/${streamSessionId}`)
      } else if (event === 'error') {
        setError((data as { message?: string }).message ?? '澄清失败')
        setPhase('idle')
      }
    })
  }

  function observeResearch(
    sid: string, connect: (onEvent: (event: string, data: unknown) => void) => () => void,
    planMsgIndex?: number,
  ) {
    setPhase('executing')
    setResearchSteps([])
    setError(null)
    setRecovery(null)
    const runId = ++sseRunIdRef.current
    sseAbortRef.current?.()
    sseAbortRef.current = connect((event, data) => {
      if (runId !== sseRunIdRef.current) return
      if (event === 'step') {
        setResearchSteps((prev) => [...prev, labelForTool((data as { node: string }).node)])
      } else if (event === 'final') {
        const final = data as ResearchFinal
        setMessages((previous) => {
          const approved = previous.map((message) => message.role === 'plan' ? { ...message, status: 'approved' as const } : message)
          return final.message_id && approved.some((message) => message.role === 'assistant' && message.id === final.message_id)
            ? approved : [...approved, { id: final.message_id ?? undefined, role: 'assistant', content: final.report_markdown, markdown: true }]
        })
        setPhase('idle')
        hasApprovedPlanRef.current = true
        if (final.failure_reason === 'search_failed') {
          setError('⚠️ 论文检索失败（arXiv 服务暂时不可达）。请稍后重试。')
        } else if (final.failure_reason === 'download_failed') {
          setError('⚠️ 论文 PDF 下载失败，且未能生成可用的摘要综述，请稍后重试。')
        } else if (final.failure_reason === 'analysis_failed') {
          setError('⚠️ 没有可用的论文分析结果或下载失败论文的摘要，未能生成综述。')
        } else if (final.failure_reason === 'review_failed') {
          setError('⚠️ 综述已生成，但自动审校失败，请人工复核引用和结论。')
        }
      } else if (event === 'error') {
        const errorData = data as { code?: string; message?: string }
        setError(errorData.message ?? '执行失败')
        setPhase('idle')
        setRecovery('reconnect')
        getPlan(sid).then(async (plan) => {
          if (runId !== sseRunIdRef.current) return
          hasApprovedPlanRef.current = plan.plan_status === 'approved'
          setRecovery(plan.execution_status === 'running' ? 'reconnect' : plan.can_resume ? 'resume' : null)
          if (plan.execution_steps) setResearchSteps(plan.execution_steps.map(labelForTool))
          if (planMsgIndex !== undefined && ['pending', 'edited', 'approved', 'rejected'].includes(plan.plan_status)) {
            setMessages((previous) => previous.map((message, index) => index === planMsgIndex && message.role === 'plan'
              ? { ...message, status: plan.plan_status as PlanMsg['status'] } : message))
          }
          if (plan.execution_status === 'done') {
            const session = await getSession(sid)
            if (runId !== sseRunIdRef.current) return
            const history: Message[] = session.messages
              .filter((message) => message.role === 'user' || message.role === 'assistant')
              .map((message) => ({ id: message.id, role: message.role as 'user' | 'assistant', content: message.content,
                markdown: message.role === 'assistant' }))
            if (plan.plan) history.push({ role: 'plan', plan: plan.plan, status: 'approved' })
            setMessages(history)
            setError(null)
          }
        }).catch(() => { /* Keep reconnect available until the server can report its state. */ })
      }
    })
  }

  function runFollowup(query: string) {
    if (sessionId) observeResearch(sessionId, (onEvent) => continueResearchStream(sessionId, query, onEvent))
  }

  function runExecute(planMsgIndex: number, editedPlan?: ResearchPlan) {
    if (!sessionId) return
    setMessages((previous) => previous.map((message, index) => index === planMsgIndex && message.role === 'plan'
      ? { ...message, status: 'approved' as const } : message))
    observeResearch(sessionId, (onEvent) => approvePlanStream(sessionId, editedPlan ?? null, onEvent), planMsgIndex)
  }

  function recoverResearch() {
    if (!sessionId) return
    const connect = recovery === 'resume' ? resumeResearchStream : researchEvents
    observeResearch(sessionId, (onEvent) => connect(sessionId, onEvent))
  }

  async function handleCancelResearch() {
    if (!sessionId) return
    try {
      await cancelResearch(sessionId)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err))
    }
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
      // 拒绝后重新澄清研究方向。
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
      } else if (clarifyActiveRef.current) {
        // M5.6: 已在澄清对话中 → 继续
        runClarify(query, true)
      } else {
        // M5.6: 首次研究方向 → 先澄清意图再生成 plan
        runClarify(query)
      }
      return
    }

    // QA 模式
    const runId = ++sseRunIdRef.current
    setPhase('planning') // 借用 phase='planning' 显示"思考中…"
    try {
      const { reply, session_id, citations } = await chatQA(query, sessionId, Array.from(selectedKBs))
      if (runId !== sseRunIdRef.current) return
      setSessionId(session_id)
      setMessages((m) => [...m, { role: 'assistant', content: reply, citations }])
      if (!routeSessionId) navigate(`/chat/${session_id}`)
    } catch (err) {
      if (runId === sseRunIdRef.current) setError(err instanceof ApiError ? err.message : String(err))
    } finally {
      if (runId === sseRunIdRef.current) setPhase('idle')
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
          {phase === 'executing' && (
            <div className="space-y-2">
              <ResearchProgress steps={researchSteps} />
              <button
                type="button"
                onClick={handleCancelResearch}
                className="rounded border border-red-200 px-3 py-1 text-xs text-red-600 hover:bg-red-50"
              >
                停止研究
              </button>
            </div>
          )}
          {phase === 'planning' && mode === 'qa' && (
            <div className="mr-auto max-w-[80%] rounded-lg bg-white px-4 py-2 text-sm text-zinc-400 shadow-sm">
              思考中…
            </div>
          )}
          {error && (
            <div className="rounded-lg bg-red-50 px-4 py-2 text-sm text-red-600">错误：{error}</div>
          )}
          {recovery && phase === 'idle' && (
            <button onClick={recoverResearch} className="rounded bg-blue-500 px-3 py-2 text-sm text-white">
              {recovery === 'resume' ? '继续未完成的研究' : '重新连接研究进度'}
            </button>
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
              [{i + 1}] {c.doc_id.slice(0, 8)} p.{c.page}{c.page_end && c.page_end !== c.page ? `–${c.page_end}` : ''}
              {c.section && ` · ${c.section}`}
              {c.figure_ids?.length ? ` · 图 ${c.figure_ids.join(', ')}` : ''}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}
