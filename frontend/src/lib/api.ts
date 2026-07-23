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
  // 204 No Content / 空 body：直接返回 undefined
  if (res.status === 204) {
    return undefined as T
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

export async function patchJson<T>(path: string, body: unknown): Promise<T> {
  return handle<T>(
    await fetch(path, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  )
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

// === Sessions（M2.6） ===

export interface SessionSummary {
  id: string
  title: string
  mode: string
  status: string
  message_count: number
  created_at: string
  updated_at: string
}

export interface SessionMessage {
  id: string
  role: string
  content: string
  extra?: string | null
  created_at: string
}

export interface SessionDetail extends SessionSummary {
  messages: SessionMessage[]
}

export function listSessions(): Promise<SessionSummary[]> {
  return getJson<SessionSummary[]>('/api/sessions')
}

export function getSession(id: string): Promise<SessionDetail> {
  return getJson<SessionDetail>(`/api/sessions/${id}`)
}

export function deleteSession(id: string): Promise<void> {
  return deleteJson(`/api/sessions/${id}`)
}

export function updateSession(id: string, title: string): Promise<SessionSummary> {
  return patchJson<SessionSummary>(`/api/sessions/${id}`, { title })
}

// === Research（M3.4，SSE 流式） ===

export interface ResearchPaper {
  arxiv_id: string
  title: string
  authors: string[]
  year: number
  abstract: string
  pdf_url?: string | null
}

export interface ResearchFinal {
  session_id: string
  report_markdown: string
  report_path: string
  papers: ResearchPaper[]
  failure_reason?: 'search_failed' | 'download_failed' | null  // M5.5.10
}

export interface ResearchBody {
  query: string
  session_id?: string
  depth?: 'quick' | 'normal' | 'deep'
  max_papers?: number
}

// 浏览器原生 EventSource 只支持 GET；研究接口是 POST+body，自己用 fetch 解析 SSE。
// 返回一个取消函数（组件卸载 / 用户中止时调用）。
export function researchStream(
  body: ResearchBody,
  onEvent: (event: string, data: unknown) => void,
): () => void {
  const ctrl = new AbortController()
  ;(async () => {
    const res = await fetch('/api/research/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify(body),
      signal: ctrl.signal,
    })
    if (!res.ok || !res.body) throw new Error(`SSE 连接失败: ${res.status}`)
    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buf = ''
    for (;;) {
      const { value, done } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      let idx: number
      while ((idx = buf.indexOf('\n\n')) !== -1) {
        const raw = buf.slice(0, idx)
        buf = buf.slice(idx + 2)
        let event = 'message'
        let data = ''
        for (const line of raw.split('\n')) {
          if (line.startsWith('event:')) event = line.slice(6).trim()
          else if (line.startsWith('data:')) data += line.slice(5).trim()
        }
        if (data) onEvent(event, JSON.parse(data))
      }
    }
  })().catch((err) => onEvent('error', { message: String(err) }))
  return () => ctrl.abort()
}

// === Plan（M5.5：用户可审可改的研究计划）===

export interface PlanSubQuestion {
  question: string
  rationale: string
}

export interface PlanSearchQueryGroup {
  intent: string
  queries: string[]
}

export interface PlanOutlineSection {
  heading: string
  bullets: string[]
}

export interface ResearchPlan {
  title: string
  sub_questions: PlanSubQuestion[]
  search_queries: PlanSearchQueryGroup[]
  outline: PlanOutlineSection[]
  estimated_papers: number
  reasoning: string
}

export type PlanStatus = 'pending' | 'edited' | 'approved' | 'rejected' | 'none'

export interface PlanResponse {
  session_id: string
  plan: ResearchPlan | null
  plan_status: PlanStatus
  plan_generated_at: string | null
}

export function getPlan(sessionId: string): Promise<PlanResponse> {
  return getJson<PlanResponse>(`/api/research/${sessionId}/plan`)
}

// startPlan: 一次性 POST 触发 planner，返 PlanResponse（plan_status="pending"）。
// ponytail：不走 SSE——plan 生成 < 5s，前端 spinner 即可，SSE 留给执行阶段。
export function startPlan(body: {
  query: string
  session_id?: string
  depth?: 'quick' | 'normal' | 'deep'
  max_papers?: number
}): Promise<PlanResponse> {
  return postJson<PlanResponse>('/api/research/plan', body)
}

export function updatePlan(
  sessionId: string,
  patch: Partial<ResearchPlan>,
): Promise<PlanResponse> {
  return patchJson<PlanResponse>(`/api/research/${sessionId}/plan`, { plan: patch })
}

export function rejectPlan(sessionId: string): Promise<PlanResponse> {
  return postJson<PlanResponse>(`/api/research/${sessionId}/plan/reject`, {})
}

// approvePlan 走 SSE（SSE 推到 step + final），复用 researchStream 的 SSE 解析器。
// 区别：endpoint 是 /api/research/{sid}/plan/approve，body 不需要 query（state 已有）。
export function approvePlanStream(
  sessionId: string,
  editedPlan: ResearchPlan | null,
  onEvent: (event: string, data: unknown) => void,
): () => void {
  const ctrl = new AbortController()
  ;(async () => {
    const res = await fetch(`/api/research/${sessionId}/plan/approve`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify(editedPlan ? { edited_plan: editedPlan } : {}),
      signal: ctrl.signal,
    })
    if (!res.ok || !res.body) throw new Error(`SSE 连接失败: ${res.status}`)
    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buf = ''
    for (;;) {
      const { value, done } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      let idx: number
      while ((idx = buf.indexOf('\n\n')) !== -1) {
        const raw = buf.slice(0, idx)
        buf = buf.slice(idx + 2)
        let event = 'message'
        let data = ''
        for (const line of raw.split('\n')) {
          if (line.startsWith('event:')) event = line.slice(6).trim()
          else if (line.startsWith('data:')) data += line.slice(5).trim()
        }
        if (data) onEvent(event, JSON.parse(data))
      }
    }
  })().catch((err) => onEvent('error', { message: String(err) }))
  return () => ctrl.abort()
}

// M5.5.6 · 追问：已 approved plan 后续问问题时跳过 planner，直接进 researcher。
// 协议与 approvePlanStream 完全一致（step + final + :done），body 改传 query。
export function continueResearchStream(
  sessionId: string,
  query: string,
  onEvent: (event: string, data: unknown) => void,
): () => void {
  const ctrl = new AbortController()
  ;(async () => {
    const res = await fetch(`/api/research/${sessionId}/continue/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify({ query }),
      signal: ctrl.signal,
    })
    if (!res.ok || !res.body) throw new Error(`SSE 连接失败: ${res.status}`)
    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buf = ''
    for (;;) {
      const { value, done } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      let idx: number
      while ((idx = buf.indexOf('\n\n')) !== -1) {
        const raw = buf.slice(0, idx)
        buf = buf.slice(idx + 2)
        let event = 'message'
        let data = ''
        for (const line of raw.split('\n')) {
          if (line.startsWith('event:')) event = line.slice(6).trim()
          else if (line.startsWith('data:')) data += line.slice(5).trim()
        }
        if (data) onEvent(event, JSON.parse(data))
      }
    }
  })().catch((err) => onEvent('error', { message: String(err) }))
  return () => ctrl.abort()
}
