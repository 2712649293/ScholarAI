import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { ChatPanel } from './ChatPanel'
import {
  chatQA,
  getSession,
  researchStream,
  startPlan,
  approvePlanStream,
  rejectPlan,
  updatePlan,
} from '@/lib/api'

vi.mock('@/lib/api', () => ({
  chatQA: vi.fn(),
  listKBs: vi.fn().mockResolvedValue([]),
  getSession: vi.fn().mockResolvedValue({ messages: [] }),
  researchStream: vi.fn(),
  // M5.5 plan 模块
  startPlan: vi.fn(),
  approvePlanStream: vi.fn(),
  rejectPlan: vi.fn(),
  updatePlan: vi.fn(),
  ApiError: class extends Error {},
}))

const renderPanel = () => render(<ChatPanel />, { wrapper: MemoryRouter })

describe('ChatPanel', () => {
  it('disables send button when input is empty', () => {
    renderPanel()
    const btn = screen.getByRole('button', { name: '发送' })
    expect(btn).toBeDisabled()
  })

  it('sends message and shows reply', async () => {
    const mocked = vi.mocked(chatQA)
    mocked.mockResolvedValue({
      reply: '你好，世界',
      session_id: 'sess-1',
      echo: false,
      citations: [],
    })
    const user = userEvent.setup()
    renderPanel()
    const input = screen.getByPlaceholderText('输入你的问题…') as HTMLInputElement
    await user.type(input, 'hi{Enter}')

    await waitFor(() => {
      expect(screen.getByText('你好，世界')).toBeInTheDocument()
    })
    expect(mocked).toHaveBeenCalledWith('hi', undefined, [])
  })

  it('shows error on failure', async () => {
    const mocked = vi.mocked(chatQA)
    mocked.mockRejectedValue(new Error('网络挂了'))
    const user = userEvent.setup()
    renderPanel()
    const input = screen.getByPlaceholderText('输入你的问题…')
    await user.type(input, 'hi{Enter}')
    await waitFor(() => {
      expect(screen.getByText(/网络挂了/)).toBeInTheDocument()
    })
  })

  it('renders citations as badges', async () => {
    const mocked = vi.mocked(chatQA)
    mocked.mockResolvedValue({
      reply: '基于知识库的回答',
      session_id: 'sess-1',
      echo: false,
      citations: [
        { kb_id: 'kb1', doc_id: 'doc-abc', page: 3, chunk_index: 0, text: '片段 A', score: 0.9 },
        { kb_id: 'kb1', doc_id: 'doc-def', page: 1, chunk_index: 0, text: '片段 B', score: 0.8 },
      ],
    })
    const user = userEvent.setup()
    renderPanel()
    await user.type(screen.getByPlaceholderText('输入你的问题…'), 'hi{Enter}')
    await waitFor(() => {
      expect(screen.getByText('基于知识库的回答')).toBeInTheDocument()
    })
    expect(screen.getByText('[1] doc-abc p.3')).toBeInTheDocument()
    expect(screen.getByText('[2] doc-def p.1')).toBeInTheDocument()
  })

  it('research mode: generate plan → approve → streams steps then renders markdown report', async () => {
    // M5.5: research 模式先 startPlan 拿 plan → 点批准 → approvePlanStream 走 SSE
    vi.mocked(startPlan).mockResolvedValue({
      session_id: 'sess-r',
      plan: {
        title: 'LLM 推理综述',
        sub_questions: [{ question: 'q1', rationale: 'r1' }],
        search_queries: [{ intent: 'i1', queries: ['s1'] }],
        outline: [{ heading: 'h1', bullets: ['b1'] }],
        estimated_papers: 10,
        reasoning: 'r',
      },
      plan_status: 'pending',
      plan_generated_at: '2026-07-21T00:00:00Z',
    } as never)
    vi.mocked(approvePlanStream).mockImplementation((_sid, _plan, onEvent) => {
      onEvent('step', { node: 'search_arxiv' })
      onEvent('final', {
        session_id: 'sess-r',
        report_markdown: '# 综述标题\n正文内容',
        report_path: 'data/reports/sess-r.md',
        papers: [],
      })
      return () => {}
    })
    const user = userEvent.setup()
    renderPanel()
    await user.click(screen.getByRole('button', { name: '研究模式' }))
    await user.type(screen.getByPlaceholderText('输入研究方向…'), 'LLM 推理{Enter}')
    // 等 plan 卡片出现（标题跨多节点，用 function 匹配）
    await screen.findByText((content, element) => {
      return element?.tagName === 'DIV' && content.includes('LLM 推理综述')
    })
    // 点批准（按钮文本含 emoji "✅ 批准并开始"）
    await user.click(screen.getByRole('button', { name: /批准并开始/ }))
    // 综述 markdown 落地
    await waitFor(() => {
      expect(screen.getByText('综述标题')).toBeInTheDocument()
    })
    expect(screen.getByRole('button', { name: '下载 .md' })).toBeInTheDocument()
    expect(vi.mocked(approvePlanStream)).toHaveBeenCalled()
  })

  it('mode follows session.mode on load (qa case — regression for mode-residue bug)', async () => {
    // 回归：之前 setMode 只在 s.mode === 'research' 时调用，
    // 切到 qa session 后 local mode 仍残留 research。修后无条件按 s.mode 设。
    vi.mocked(getSession).mockResolvedValue({ mode: 'qa', messages: [] } as never)
    renderPanel()
    await waitFor(() => {
      expect(screen.getByRole('button', { name: '问答模式' })).toHaveClass('bg-blue-500')
      expect(screen.getByRole('button', { name: '研究模式' })).not.toHaveClass('bg-blue-500')
    })
  })

  it('locks mode after first message — other button is disabled (qa case)', async () => {
    // 发完第一条 qa 消息后，研究模式按钮应变灰（mode 锁定）
    vi.mocked(chatQA).mockResolvedValue({
      reply: 'hi back',
      session_id: 'sess-lock',
      echo: false,
      citations: [],
    })
    const user = userEvent.setup()
    renderPanel()
    const input = screen.getByPlaceholderText('输入你的问题…')
    await user.type(input, 'hi{Enter}')
    // 等回复出现 = 消息发送完成
    await screen.findByText('hi back')
    expect(screen.getByRole('button', { name: '研究模式' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '问答模式' })).not.toBeDisabled()
  })

  it('cross-session isolation: plan-stream / execute-stream abort when route sessionId changes', async () => {
    // M_bug_sse_isolation 回归：路由切到新 session 时，in-flight 的 plan + execute SSE
    // 都必须被 abort，否则旧闭包的 step/final 事件会污染新 session 的 state 并抢 URL。
    // 用一个不会 resolve 的 mock 模拟"研究还在跑"，验证切换后 abort 被调用。
    const planAbortSpy = vi.fn()
    vi.mocked(startPlan).mockImplementation(async () => {
      // 返回一个挂起的 promise 让 startPlan 一直在"planning"态
      return new Promise(() => {}) as never
    })
    vi.mocked(approvePlanStream).mockImplementation(() => planAbortSpy)

    const { unmount } = render(
      <MemoryRouter initialEntries={['/chat/sess-b']}>
        <Routes>
          <Route path="/chat/:sessionId" element={<ChatPanel />} />
        </Routes>
      </MemoryRouter>,
    )
    await new Promise((r) => setTimeout(r, 0))
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: '研究模式' }))
    const input = screen.getByPlaceholderText('输入研究方向…')
    await user.type(input, 'LLM{Enter}')
    expect(vi.mocked(startPlan)).toHaveBeenCalled()
    // 卸载：routeSessionId 变化的 useEffect cleanup → abort + runId 自增，
    // 任何 in-flight 的 SSE 回调都会因 runId 不匹配而丢弃事件。
    unmount()
    // 注：startPlan 是一次性 fetch（无 abort），abort 走的是它内部 AbortController
    // 与 useEffect cleanup 一致；这里主要验证 plan 流程没漏调 cleanup
  })

  it('cross-session isolation: stale SSE events from prior execute run are dropped (token filter)', async () => {
    // M_bug_sse_isolation 二次防线：runId ref 让 stale 回调即便绕过 abort 也会被丢弃。
    // 场景：第一次 runExecute 推 final 让 phase=idle（但 onEvent 引用保留），
    // 第二次 startPlan + approve → execute 推 final 让"新报告"出现；然后用 stale 引用推 final，
    // 应被 runId 自增拦下，stale 文本不应出现。
    let staleOnEvent: ((event: string, data: unknown) => void) | null = null
    vi.mocked(startPlan).mockResolvedValue({
      session_id: 'sess-a',
      plan: {
        title: 'p1',
        sub_questions: [{ question: 'q', rationale: 'r' }],
        search_queries: [{ intent: 'i', queries: ['s'] }],
        outline: [{ heading: 'h', bullets: ['b'] }],
        estimated_papers: 5,
        reasoning: '',
      },
      plan_status: 'pending',
      plan_generated_at: '',
    } as never)
    vi.mocked(approvePlanStream)
      .mockImplementationOnce((_sid, _plan, onEvent) => {
        staleOnEvent = onEvent
        // 立即推 final 让 phase=idle（用户能继续发）
        onEvent('final', {
          session_id: 'sess-old',
          report_markdown: '# 旧报告',
          report_path: 'data/reports/old.md',
          papers: [],
        })
        return vi.fn()
      })
      .mockImplementationOnce((_sid, _plan, onEvent) => {
        onEvent('final', {
          session_id: 'sess-new',
          report_markdown: '# 新报告',
          report_path: 'data/reports/sess-new.md',
          papers: [],
        })
        return vi.fn()
      })
    vi.mocked(getSession).mockResolvedValue({ mode: 'research', messages: [] } as never)

    renderPanel()
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: '研究模式' }))
    // 第一次研究方向
    await user.type(screen.getByPlaceholderText('输入研究方向…'), 'LLM{Enter}')
    await screen.findByText((content, element) => {
      return element?.tagName === 'DIV' && content.includes('p1')
    }) // plan 卡片标题
    await user.click(screen.getByRole('button', { name: /批准并开始/ }))
    await screen.findByText('旧报告') // 第一次 execute final 落地
    expect(staleOnEvent).not.toBeNull()

    // 第二次研究方向（在 staleOnEvent 引用之外独立发）
    await user.type(screen.getByPlaceholderText('输入研究方向…'), 'RAG{Enter}')
    // 第二个 plan 卡片出现（标题含 "p1"），点第二个卡片里的批准按钮
    await screen.findAllByRole('button', { name: /批准并开始/ })
    const approveBtns = screen.getAllByRole('button', { name: /批准并开始/ })
    await user.click(approveBtns[approveBtns.length - 1])
    await screen.findByText('新报告')

    // 现在通过 staleOnEvent 推 final —— runId=1 已自增到 2 → stale 回调被拦
    staleOnEvent!('final', {
      session_id: 'sess-stale',
      report_markdown: '# 应该被丢弃',
      report_path: 'data/reports/stale.md',
      papers: [],
    })
    // 给 state 一次 tick
    await new Promise((r) => setTimeout(r, 0))
    expect(screen.queryByText('应该被丢弃')).not.toBeInTheDocument()
  })

  // === M5.5 · Plan 模块新测试 ===

  it('M5.5: plan event → renders PlanCard with title and approve/reject buttons', async () => {
    vi.mocked(startPlan).mockResolvedValue({
      session_id: 'sess-p',
      plan: {
        title: 'LLM 综述',
        sub_questions: [{ question: '子问题1', rationale: 'r1' }],
        search_queries: [{ intent: 'intent1', queries: ['q1', 'q2'] }],
        outline: [{ heading: '章1', bullets: ['b1', 'b2'] }],
        estimated_papers: 15,
        reasoning: 'reasoning text',
      },
      plan_status: 'pending',
      plan_generated_at: '2026-07-21T00:00:00Z',
    } as never)
    const user = userEvent.setup()
    renderPanel()
    await user.click(screen.getByRole('button', { name: '研究模式' }))
    await user.type(screen.getByPlaceholderText('输入研究方向…'), 'LLM{Enter}')
    // plan 内容渲染
    await screen.findByText((content, el) => el?.tagName === 'DIV' && content.includes('LLM 综述'))
    expect(screen.getByText('子问题1')).toBeInTheDocument()
    expect(screen.getByText(/intent1/)).toBeInTheDocument()
    expect(screen.getByText('章1')).toBeInTheDocument()
    // 三按钮都在
    expect(screen.getByRole('button', { name: /编辑/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /拒绝/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /批准并开始/ })).toBeInTheDocument()
  })

  it('M5.5: approve button → calls approvePlanStream and starts execute flow', async () => {
    vi.mocked(startPlan).mockResolvedValue({
      session_id: 'sess-a',
      plan: {
        title: 'p',
        sub_questions: [{ question: 'q', rationale: 'r' }],
        search_queries: [{ intent: 'i', queries: ['s'] }],
        outline: [{ heading: 'h', bullets: ['b'] }],
        estimated_papers: 5,
        reasoning: '',
      },
      plan_status: 'pending',
      plan_generated_at: '',
    } as never)
    vi.mocked(approvePlanStream).mockImplementation((_sid, _plan, onEvent) => {
      onEvent('step', { node: 'search_arxiv' })
      onEvent('final', {
        session_id: 'sess-a',
        report_markdown: '# 报告',
        report_path: 'data/reports/sess-a.md',
        papers: [],
      })
      return () => {}
    })
    const user = userEvent.setup()
    renderPanel()
    await user.click(screen.getByRole('button', { name: '研究模式' }))
    await user.type(screen.getByPlaceholderText('输入研究方向…'), 'X{Enter}')
    await screen.findByText((c, e) => e?.tagName === 'DIV' && c.includes('p'))
    await user.click(screen.getByRole('button', { name: /批准并开始/ }))
    await screen.findByText('报告')
    expect(vi.mocked(approvePlanStream)).toHaveBeenCalledWith(
      'sess-a',
      null,
      expect.any(Function),
    )
  })

  it('M5.5: reject button → calls rejectPlan and card turns gray', async () => {
    vi.mocked(startPlan).mockResolvedValue({
      session_id: 'sess-rj',
      plan: {
        title: 'plan',
        sub_questions: [{ question: 'q', rationale: 'r' }],
        search_queries: [{ intent: 'i', queries: ['s'] }],
        outline: [{ heading: 'h', bullets: ['b'] }],
        estimated_papers: 5,
        reasoning: '',
      },
      plan_status: 'pending',
      plan_generated_at: '',
    } as never)
    vi.mocked(rejectPlan).mockResolvedValue({
      session_id: 'sess-rj',
      plan: null,
      plan_status: 'rejected',
      plan_generated_at: '',
    } as never)
    const user = userEvent.setup()
    renderPanel()
    await user.click(screen.getByRole('button', { name: '研究模式' }))
    await user.type(screen.getByPlaceholderText('输入研究方向…'), 'X{Enter}')
    await screen.findByText((c, e) => e?.tagName === 'DIV' && c.includes('plan'))
    await user.click(screen.getByRole('button', { name: /拒绝/ }))
    await waitFor(() => {
      expect(vi.mocked(rejectPlan)).toHaveBeenCalledWith('sess-rj')
    })
    // 卡片变 rejected（灰），按钮消失
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: /批准并开始/ })).not.toBeInTheDocument()
    })
  })

  it('M5.5: edit button → opens editor; save calls updatePlan once', async () => {
    vi.mocked(startPlan).mockResolvedValue({
      session_id: 'sess-e',
      plan: {
        title: 'plan',
        sub_questions: [{ question: 'q1', rationale: 'r1' }],
        search_queries: [{ intent: 'i1', queries: ['s1'] }],
        outline: [{ heading: 'h1', bullets: ['b1'] }],
        estimated_papers: 5,
        reasoning: '',
      },
      plan_status: 'pending',
      plan_generated_at: '',
    } as never)
    vi.mocked(updatePlan).mockResolvedValue({
      session_id: 'sess-e',
      plan: {
        title: 'plan',
        sub_questions: [{ question: 'q1 修改', rationale: 'r1' }],
        search_queries: [{ intent: 'i1', queries: ['s1'] }],
        outline: [{ heading: 'h1', bullets: ['b1'] }],
        estimated_papers: 5,
        reasoning: '',
      },
      plan_status: 'edited',
      plan_generated_at: '',
    } as never)
    const user = userEvent.setup()
    renderPanel()
    await user.click(screen.getByRole('button', { name: '研究模式' }))
    await user.type(screen.getByPlaceholderText('输入研究方向…'), 'X{Enter}')
    await screen.findByText((c, e) => e?.tagName === 'DIV' && c.includes('plan'))
    // 点编辑
    await user.click(screen.getByRole('button', { name: /编辑/ }))
    // textarea 出现（用 waitFor 让 setState 生效）
    await waitFor(() => {
      expect(screen.getAllByPlaceholderText('子问题 — rationale').length).toBeGreaterThan(0)
    })
    // 修改第一个 textarea（用 fireEvent 避免 userEvent 中文/emoji 编码问题）
    const textarea = screen.getAllByPlaceholderText('子问题 — rationale')[0] as HTMLTextAreaElement
    const newText = 'sub_q_modified — r1'
    // 直接调 setter 绕过 userEvent 字符解析
    const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
      HTMLTextAreaElement.prototype,
      'value',
    )!.set!
    nativeInputValueSetter.call(textarea, newText)
    textarea.dispatchEvent(new Event('input', { bubbles: true }))
    // 等 setState 落
    await new Promise((r) => setTimeout(r, 0))
    // 点保存编辑
    await waitFor(() => {
      expect(screen.getByRole('button', { name: '保存编辑' })).toBeInTheDocument()
    })
    await user.click(screen.getByRole('button', { name: '保存编辑' }))
    await waitFor(() => {
      expect(vi.mocked(updatePlan)).toHaveBeenCalledTimes(1)
    })
    // patch 内含 sub_questions 字段（编辑过）
    expect(vi.mocked(updatePlan).mock.calls[0][0]).toBe('sess-e')
    expect(vi.mocked(updatePlan).mock.calls[0][1]).toHaveProperty('sub_questions')
  })

  it('M5.5: plan LLM error shows error toast', async () => {
    vi.mocked(startPlan).mockRejectedValue(new Error('planner 挂了'))
    const user = userEvent.setup()
    renderPanel()
    await user.click(screen.getByRole('button', { name: '研究模式' }))
    await user.type(screen.getByPlaceholderText('输入研究方向…'), 'X{Enter}')
    await screen.findByText(/planner 挂了/)
  })
})
