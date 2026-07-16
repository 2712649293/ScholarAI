import { render, screen } from '@testing-library/react'
import { describe, it, expect } from 'vitest'
import { ChatPage } from './ChatPage'

describe('ChatPage', () => {
  it('renders ScholarAI heading', () => {
    render(<ChatPage />)
    expect(screen.getByRole('heading', { name: /ScholarAI/i })).toBeInTheDocument()
  })
})
