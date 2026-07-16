import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import { ChatPage } from './ChatPage'

describe('ChatPage', () => {
  it('renders chat input placeholder', () => {
    render(<ChatPage />)
    expect(screen.getByPlaceholderText('输入你的问题…')).toBeInTheDocument()
  })
})
