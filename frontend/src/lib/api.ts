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
      const detail = typeof body.detail === 'object' && body.detail !== null && !Array.isArray(body.detail)
        ? body.detail as { code?: string; message?: string } : undefined
      code = body.code ?? detail?.code
      message = body.message ?? detail?.message ?? (typeof body.detail === 'string' ? body.detail : message)
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
  page_end?: number | null
  section?: string
  figure_ids?: string[]
  chunk_id?: string
  rerank_score?: number | null
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
  processing_stage?: string
  warnings?: string[]
  created_at: string
  indexed_at?: string | null
}

export interface SearchHit {
  text: string
  doc_id: string
  page: number
  chunk_index: number
  score: number
  page_end?: number | null
  section?: string
  figure_ids?: string[]
  rerank_score?: number | null
  retrieval_warnings?: string[]
}

export function retryDoc(kbId: string, docId: string): Promise<Doc> {
  return postJson<Doc>(`/api/knowledge/${kbId}/docs/${docId}/retry`, {})
}

export function deleteDoc(kbId: string, docId: string): Promise<void> {
  return deleteJson(`/api/knowledge/${kbId}/docs/${docId}`)
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
  message_id?: string | null
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

// POST SSE 共用解析器，支持分块 UTF-8、CRLF 与多行 data；主动取消静默结束。
function postSSE(
  path: string, body: unknown,
  onEvent: (event: string, data: unknown) => void,
  method: 'GET' | 'POST' = 'POST',
): () => void {
  const ctrl = new AbortController()
  ;(async () => {
    const res = await fetch(path, {
      method,
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify(body), signal: ctrl.signal,
    })
    if (!res.ok) await handle(res)
    if (!res.body) throw new Error('SSE 响应为空')
    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let completed = false
    try {
      for (;;) {
        const { value, done } = await reader.read()
        buffer += done ? decoder.decode() : decoder.decode(value, { stream: true })
        buffer = buffer.replace(/\r\n/g, '\n')
        let end: number
        while ((end = buffer.indexOf('\n\n')) !== -1) {
          const raw = buffer.slice(0, end)
          buffer = buffer.slice(end + 2)
          let event = 'message'
          const lines: string[] = []
          for (const line of raw.split('\n')) {
            if (line.startsWith('event:')) event = line.slice(6).trim()
            else if (line.startsWith('data:')) lines.push(line.slice(5).replace(/^ /, ''))
          }
          if (lines.length && !ctrl.signal.aborted) {
            if (['final', 'complete', 'confirmed', 'error'].includes(event)) completed = true
            onEvent(event, JSON.parse(lines.join('\n')))
          }
        }
        if (done) break
      }
      if (!completed && !ctrl.signal.aborted) throw new Error('SSE 连接提前结束，请重试')
    } finally {
      reader.releaseLock()
    }
  })().catch((err) => {
    if (!ctrl.signal.aborted) onEvent('error', { message: err instanceof Error ? err.message : String(err) })
  })
  return () => ctrl.abort()
}

export function researchStream(
  body: ResearchBody, onEvent: (event: string, data: unknown) => void,
): () => void {
  return postSSE('/api/research/stream', body, onEvent)
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

export type PlanStatus = 'pending' | 'edited' | 'approved' | 'rejected' | 'none' | 'clarifying'

export interface PlanResponse {
  session_id: string
  plan: ResearchPlan | null
  plan_status: PlanStatus
  plan_generated_at: string | null
  execution_status?: 'idle' | 'running' | 'done' | 'failed' | 'interrupted'
  execution_steps?: string[]
  execution_error?: string | null
  can_resume?: boolean
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

export function approvePlanStream(
  sessionId: string, editedPlan: ResearchPlan | null,
  onEvent: (event: string, data: unknown) => void,
): () => void {
  return postSSE(`/api/research/${sessionId}/plan/approve`, editedPlan ? { edited_plan: editedPlan } : {}, onEvent)
}

export interface ClarifyConfirmed {
  session_id: string
  clarify_direction: { refined_query: string; year_start: number; year_end: number; notes: string; confirmed_at: string }
  plan_status: 'pending'
}

export function clarifyStream(
  body: { query: string; session_id?: string },
  onEvent: (event: string, data: unknown) => void,
): () => void {
  return postSSE('/api/research/clarify/stream', body, onEvent)
}

export function clarifyContinueStream(
  sessionId: string, query: string, onEvent: (event: string, data: unknown) => void,
): () => void {
  return postSSE(`/api/research/${sessionId}/clarify/continue`, { query }, onEvent)
}

export function continueResearchStream(
  sessionId: string, query: string, onEvent: (event: string, data: unknown) => void,
): () => void {
  return postSSE(`/api/research/${sessionId}/continue/stream`, { query }, onEvent)
}

export function researchEvents(
  sessionId: string, onEvent: (event: string, data: unknown) => void,
): () => void {
  return postSSE(`/api/research/${sessionId}/events`, undefined, onEvent, 'GET')
}

export function resumeResearchStream(
  sessionId: string, onEvent: (event: string, data: unknown) => void,
): () => void {
  return postSSE(`/api/research/${sessionId}/resume/stream`, {}, onEvent)
}
