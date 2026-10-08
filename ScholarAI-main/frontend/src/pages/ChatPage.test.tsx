import { render, screen } from '@testing-library/react'
import { describe, it, expect, vi } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import { ChatPage } from './ChatPage'

vi.mock('@/lib/api', () => ({
  chatQA: vi.fn(),
  listKBs: vi.fn().mockResolvedValue([]),
  getSession: vi.fn().mockResolvedValue({ messages: [] }),
  researchStream: vi.fn(),
  ApiError: class extends Error {},
}))

describe('ChatPage', () => {
  it('renders chat input placeholder', () => {
    render(<ChatPage />, { wrapper: MemoryRouter })
    expect(screen.getByPlaceholderText('输入你的问题…')).toBeInTheDocument()
  })
})
