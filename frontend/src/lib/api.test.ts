import { describe, it, expect, vi, beforeEach } from 'vitest'
import { chatQA, ApiError, listKBs, createKB, deleteKB, researchStream } from './api'

describe('api.chatQA', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('sends POST with query only when no session/kb', async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ reply: '你好', session_id: 's1', echo: false, citations: [] }),
    })
    vi.stubGlobal('fetch', mockFetch)

    const res = await chatQA('hi')
    expect(res.reply).toBe('你好')
    expect(mockFetch).toHaveBeenCalledWith('/api/chat/qa', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: 'hi', session_id: undefined, kb_ids: undefined }),
    })
  })

  it('passes kb_ids when provided', async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ reply: 'r', session_id: 's1', echo: false, citations: [] }),
    })
    vi.stubGlobal('fetch', mockFetch)
    await chatQA('q', 'sess-1', ['kb-a', 'kb-b'])
    const call = mockFetch.mock.calls[0]
    const body = JSON.parse(call[1].body)
    expect(body.kb_ids).toEqual(['kb-a', 'kb-b'])
    expect(body.session_id).toBe('sess-1')
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

describe('SSE transport', () => {
  it('parses split UTF-8, CRLF and multiline JSON data', async () => {
    const bytes = new TextEncoder().encode('event: final\r\ndata: {\r\ndata: "text": "中文"}\r\n\r\n')
    const stream = new ReadableStream({
      start(controller) {
        for (const byte of bytes) controller.enqueue(Uint8Array.of(byte))
        controller.close()
      },
    })
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(stream)))
    const event = await new Promise<[string, unknown]>((resolve) => {
      researchStream({ query: 'q' }, (name, data) => resolve([name, data]))
    })
    expect(event).toEqual(['final', { text: '中文' }])
  })

  it('reports a premature EOF so the UI does not stay busy', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(': keepalive\n\n')))
    const event = await new Promise<[string, unknown]>((resolve) => {
      researchStream({ query: 'q' }, (name, data) => resolve([name, data]))
    })
    expect(event).toEqual(['error', { message: 'SSE 连接提前结束，请重试' }])
  })

  it('does not emit an error on intentional cancellation', async () => {
    vi.stubGlobal('fetch', vi.fn().mockImplementation((_path, options) => new Promise((_resolve, reject) => {
      options.signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
    })))
    const onEvent = vi.fn()
    researchStream({ query: 'q' }, onEvent)()
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(onEvent).not.toHaveBeenCalled()
  })

  it('shows the backend HTTPException detail message', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: { code: 'invalid_pdf', message: '不是合法 PDF 文件' } }), { status: 400 })))
    await expect(createKB('x')).rejects.toMatchObject({ code: 'invalid_pdf', message: '不是合法 PDF 文件' })
  })
})

describe('api.knowledge', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })

  it('listKBs sends GET', async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => [{ id: 'k1', name: 'test' }],
    })
    vi.stubGlobal('fetch', mockFetch)
    const res = await listKBs()
    expect(res).toHaveLength(1)
    expect(mockFetch).toHaveBeenCalledWith('/api/knowledge')
  })

  it('createKB sends POST with name', async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ id: 'k1', name: 'my kb' }),
    })
    vi.stubGlobal('fetch', mockFetch)
    await createKB('my kb')
    expect(mockFetch).toHaveBeenCalledWith('/api/knowledge', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: 'my kb' }),
    })
  })

  it('deleteKB sends DELETE and handles 204', async () => {
    // 模拟 204 No Content：body 为空，.json() 会抛
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 204,
      headers: { get: (k: string) => (k.toLowerCase() === 'content-length' ? '0' : null) },
      json: async () => { throw new SyntaxError('unexpected end of data') },
    })
    vi.stubGlobal('fetch', mockFetch)
    await expect(deleteKB('k1')).resolves.toBeUndefined()
    expect(mockFetch).toHaveBeenCalledWith('/api/knowledge/k1', { method: 'DELETE' })
  })
})
