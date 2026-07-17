import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { ChatPanel } from './ChatPanel'
import { chatQA, researchStream } from '@/lib/api'

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
      onEvent('step', { node: 'planner' })
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
})
