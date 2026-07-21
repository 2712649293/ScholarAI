import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { ChatPanel } from './ChatPanel'
import { chatQA, getSession, researchStream } from '@/lib/api'

vi.mock('@/lib/api', () => ({
  chatQA: vi.fn(),
  listKBs: vi.fn().mockResolvedValue([]),
  getSession: vi.fn().mockResolvedValue({ messages: [] }),
  researchStream: vi.fn(),
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

  it('research mode: streams steps then renders markdown report', async () => {
    vi.mocked(researchStream).mockImplementation((_body, onEvent) => {
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
    await waitFor(() => {
      expect(screen.getByText('综述标题')).toBeInTheDocument()
    })
    expect(screen.getByRole('button', { name: '下载 .md' })).toBeInTheDocument()
    expect(vi.mocked(researchStream)).toHaveBeenCalled()
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

  it('cross-session isolation: research-stream abort is called when route sessionId changes', async () => {
    // M_bug_sse_isolation 回归：路由切到新 session 时，in-flight 的 research SSE
    // 必须被 abort，否则旧闭包的 step/final 事件会污染新 session 的 state 并抢 URL。
    // 用一个不会 resolve 的 mock 模拟"研究还在跑"，验证切换后 abort 被调用。
    const abortSpy = vi.fn()
    vi.mocked(researchStream).mockImplementation((_body, _onEvent) => {
      return abortSpy
    })
    vi.mocked(getSession).mockResolvedValue({
      mode: 'research',
      messages: [{ role: 'user', content: 'old q', created_at: '' }],
    } as never)

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
    expect(vi.mocked(researchStream)).toHaveBeenCalled()
    // 卸载：routeSessionId 变化的 useEffect cleanup → abort + runId 自增，
    // 任何 in-flight 的 SSE 回调都会因 runId 不匹配而丢弃事件。
    unmount()
    expect(abortSpy).toHaveBeenCalled()
  })

  it('cross-session isolation: stale SSE events from prior run are dropped (token filter)', async () => {
    // M_bug_sse_isolation 二次防线：runId ref 让 stale 回调即便绕过 abort 也会被丢弃。
    // 场景：第一次 runResearch 推 final 让 loading=false（但 onEvent 引用保留），
    // 第二次 runResearch 也推 final 让"新报告"出现；然后用 stale 引用推 final，
    // 应被 runId 自增拦下，stale 文本不应出现。
    let staleOnEvent: ((event: string, data: unknown) => void) | null = null
    vi.mocked(researchStream).mockImplementationOnce((_body, onEvent) => {
      staleOnEvent = onEvent
      // 立即推 final 让 loading=false（用户能继续发）
      onEvent('final', {
        session_id: 'sess-old',
        report_markdown: '# 旧报告',
        report_path: 'data/reports/old.md',
        papers: [],
      })
      return vi.fn()
    })
    vi.mocked(researchStream).mockImplementationOnce((_body, onEvent) => {
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
    await screen.findByText('旧报告') // 第一次 final 落地
    expect(staleOnEvent).not.toBeNull()

    // 第二次研究方向（在 staleOnEvent 引用之外独立发）
    await user.type(screen.getByPlaceholderText('输入研究方向…'), 'RAG{Enter}')
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
})
