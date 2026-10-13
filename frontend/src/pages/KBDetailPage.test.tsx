import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Routes, Route } from 'react-router-dom'
import { vi, expect, it, beforeEach } from 'vitest'
import { KBDetailPage } from './KBDetailPage'
import { getKB, listDocs, retryDoc, deleteDoc, searchKB } from '@/lib/api'

vi.mock('@/lib/api', () => ({
  getKB: vi.fn(), listDocs: vi.fn(), retryDoc: vi.fn(), deleteDoc: vi.fn(),
  uploadDoc: vi.fn(), searchKB: vi.fn(), ApiError: class extends Error {},
}))

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(getKB).mockResolvedValue({ id: 'kb', name: '论文库', embedding_model: 'test', doc_count: 1, vector_count: 1, created_at: '' })
  vi.mocked(listDocs).mockResolvedValue([{ id: 'doc', kb_id: 'kb', filename: 'paper.pdf', page_count: 3,
    status: 'failed', processing_stage: 'vision', warnings: ['图片描述失败，保留原图与图注'], created_at: '' }])
})

const show = () => render(<MemoryRouter initialEntries={['/knowledge/kb']}><Routes>
  <Route path="/knowledge/:id" element={<KBDetailPage />} />
</Routes></MemoryRouter>)

it('shows optional image warning and sends retry and delete to the correct document', async () => {
  show()
  await screen.findByText('paper.pdf')
  expect(screen.getByText(/图片描述失败/)).toBeInTheDocument()
  const user = userEvent.setup()
  await user.click(screen.getByRole('button', { name: '重新处理' }))
  await waitFor(() => expect(retryDoc).toHaveBeenCalledWith('kb', 'doc'))
  await user.click(screen.getByRole('button', { name: '删除' }))
  await waitFor(() => expect(deleteDoc).toHaveBeenCalledWith('kb', 'doc'))
})

it('shows page range, figure provenance and external rerank score', async () => {
  vi.mocked(searchKB).mockResolvedValue([{ text: 'Matched figure description', doc_id: 'document123', page: 2,
    page_end: 3, section: 'Results', figure_ids: ['p3-fig1'], score: .032, rerank_score: .9, chunk_index: 0 }])
  show()
  await screen.findByText('paper.pdf')
  const user = userEvent.setup()
  await user.type(screen.getByPlaceholderText('输入问题…'), 'Accuracy')
  await user.click(screen.getByRole('button', { name: '搜索' }))
  await screen.findByText('Matched figure description')
  expect(screen.getByText(/p\.2–3/)).toHaveTextContent('Results')
  expect(screen.getByText(/p3-fig1/)).toHaveTextContent('重排分数 0.900')
})
