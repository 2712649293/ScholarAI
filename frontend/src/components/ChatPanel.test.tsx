import { describe, it, expect, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { ChatPanel } from './ChatPanel'
import { chatQA } from '@/lib/api'

vi.mock('@/lib/api', () => ({ chatQA: vi.fn(), ApiError: class extends Error {} }))

describe('ChatPanel', () => {
  it('disables send button when input is empty', () => {
    render(<ChatPanel />)
    const btn = screen.getByRole('button', { name: '发送' })
    expect(btn).toBeDisabled()
  })

  it('sends message and shows reply', async () => {
    const mocked = vi.mocked(chatQA)
    mocked.mockResolvedValue({ reply: '你好，世界', session_id: 'sess-1', echo: false })
    const user = userEvent.setup()
    render(<ChatPanel />)
    const input = screen.getByPlaceholderText('输入你的问题…') as HTMLInputElement
    await user.type(input, 'hi{Enter}')

    await waitFor(() => {
      expect(screen.getByText('你好，世界')).toBeInTheDocument()
    })
    expect(mocked).toHaveBeenCalledWith('hi', undefined)
  })

  it('shows error on failure', async () => {
    const mocked = vi.mocked(chatQA)
    mocked.mockRejectedValue(new Error('网络挂了'))
    const user = userEvent.setup()
    render(<ChatPanel />)
    const input = screen.getByPlaceholderText('输入你的问题…')
    await user.type(input, 'hi{Enter}')
    await waitFor(() => {
      expect(screen.getByText(/网络挂了/)).toBeInTheDocument()
    })
  })
})
