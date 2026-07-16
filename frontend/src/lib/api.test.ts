import { describe, it, expect, vi, beforeEach } from 'vitest'
import { chatQA, ApiError } from './api'

describe('api.chatQA', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('sends POST and returns reply', async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ reply: '你好', echo: true }),
    })
    vi.stubGlobal('fetch', mockFetch)

    const res = await chatQA('hi')
    expect(res.reply).toBe('你好')
    expect(mockFetch).toHaveBeenCalledWith('/api/chat/qa', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: 'hi' }),
    })
  })

  it('throws ApiError on non-ok response', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 422,
        json: async () => ({ code: 'invalid_query', message: '问题为空' }),
      }),
    )
    await expect(chatQA('')).rejects.toThrow(ApiError)
  })
})
