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

export async function postJson<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    let code: string | undefined
    try {
      const errBody = (await res.json()) as { code?: string; message?: string }
      code = errBody.code
      throw new ApiError(errBody.message ?? `HTTP ${res.status}`, res.status, code)
    } catch (e) {
      if (e instanceof ApiError) throw e
      throw new ApiError(`HTTP ${res.status}`, res.status)
    }
  }
  return (await res.json()) as T
}

export interface ChatRequest {
  query: string
}

export interface ChatResponse {
  reply: string
  echo: boolean
}

export function chatQA(query: string): Promise<ChatResponse> {
  return postJson<ChatResponse>('/api/chat/qa', { query })
}
