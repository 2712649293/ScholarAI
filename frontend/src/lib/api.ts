// 薄封装 fetch：所有请求走相对路径，开发时由 vite proxy 转到后端
export class ApiError extends Error {
  status: number
  code?: string
  constructor(message: string, status: number, code?: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
  }
}

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let code: string | undefined
    let message = `HTTP ${res.status}`
    try {
      const body = (await res.json()) as { code?: string; message?: string; detail?: unknown }
      code = body.code
      message = body.message ?? message
    } catch {
      // ignore parse error
    }
    throw new ApiError(message, res.status, code)
  }
  return (await res.json()) as T
}

export async function getJson<T>(path: string): Promise<T> {
  return handle<T>(await fetch(path))
}

export async function postJson<T>(path: string, body: unknown): Promise<T> {
  return handle<T>(
    await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  )
}

export async function deleteJson<T = void>(path: string): Promise<T> {
  return handle<T>(await fetch(path, { method: 'DELETE' }))
}

// === Chat ===

export interface ChatRequest {
  query: string
  session_id?: string
  max_history?: number
  kb_ids?: string[]
}

export interface Citation {
  kb_id: string
  doc_id: string
  page: number
  chunk_index: number
  text: string
  score: number
}

export interface ChatResponse {
  reply: string
  session_id: string
  echo: boolean
  citations: Citation[]
}

export function chatQA(query: string, sessionId?: string, kbIds?: string[]): Promise<ChatResponse> {
  return postJson<ChatResponse>('/api/chat/qa', {
    query,
    session_id: sessionId,
    kb_ids: kbIds && kbIds.length ? kbIds : undefined,
  })
}

// === Knowledge Base ===

export interface KB {
  id: string
  name: string
  embedding_model: string
  doc_count: number
  vector_count: number
  created_at: string
}

export interface Doc {
  id: string
  kb_id: string
  filename: string
  page_count: number
  status: 'pending' | 'indexed' | 'failed'
  error?: string | null
  created_at: string
  indexed_at?: string | null
}

export interface SearchHit {
  text: string
  doc_id: string
  page: number
  chunk_index: number
  score: number
}

export function listKBs(): Promise<KB[]> {
  return getJson<KB[]>('/api/knowledge')
}

export function getKB(id: string): Promise<KB> {
  return getJson<KB>(`/api/knowledge/${id}`)
}

export function createKB(name: string): Promise<KB> {
  return postJson<KB>('/api/knowledge', { name })
}

export function deleteKB(id: string): Promise<void> {
  return deleteJson(`/api/knowledge/${id}`)
}

export function listDocs(kbId: string): Promise<Doc[]> {
  return getJson<Doc[]>(`/api/knowledge/${kbId}/docs`)
}

export function getDoc(kbId: string, docId: string): Promise<Doc> {
  return getJson<Doc>(`/api/knowledge/${kbId}/docs/${docId}`)
}

export async function uploadDoc(kbId: string, file: File): Promise<Doc> {
  const form = new FormData()
  form.append('file', file)
  return handle<Doc>(
    await fetch(`/api/knowledge/${kbId}/docs`, { method: 'POST', body: form }),
  )
}

export function searchKB(kbId: string, query: string, k = 5): Promise<SearchHit[]> {
  return postJson<SearchHit[]>(`/api/knowledge/${kbId}/search`, { query, k })
}
