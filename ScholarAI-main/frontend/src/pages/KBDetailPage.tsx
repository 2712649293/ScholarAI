import { useEffect, useState, type FormEvent } from 'react'
import { useParams, Link } from 'react-router-dom'
import {
  getKB,
  listDocs,
  searchKB,
  uploadDoc,
  type Doc,
  type KB,
  type SearchHit,
  ApiError,
} from '@/lib/api'

export function KBDetailPage() {
  const { id } = useParams<{ id: string }>()
  const [kb, setKB] = useState<KB | null>(null)
  const [docs, setDocs] = useState<Doc[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)
  const [searchQuery, setSearchQuery] = useState('')
  const [hits, setHits] = useState<SearchHit[]>([])

  useEffect(() => {
    if (!id) return
    load()
    const t = setInterval(loadDocs, 2000)  // 轮询 doc 状态
    return () => clearInterval(t)
  }, [id])

  async function load() {
    if (!id) return
    try {
      const [k, d] = await Promise.all([getKB(id), listDocs(id)])
      setKB(k)
      setDocs(d)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  async function loadDocs() {
    if (!id) return
    try {
      setDocs(await listDocs(id))
    } catch {
      // 静默轮询失败
    }
  }

  async function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file || !id) return
    setUploading(true)
    setError(null)
    try {
      await uploadDoc(id, file)
      e.target.value = ''  // 清空 input
      await loadDocs()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setUploading(false)
    }
  }

  async function handleSearch(e: FormEvent) {
    e.preventDefault()
    if (!searchQuery.trim() || !id) return
    try {
      setHits(await searchKB(id, searchQuery, 5))
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    }
  }

  if (loading) return <p className="p-6 text-sm text-zinc-500">加载中…</p>
  if (!kb) return <p className="p-6 text-sm text-red-600">知识库不存在</p>

  return (
    <div className="mx-auto max-w-4xl p-6">
      <Link to="/knowledge" className="mb-2 inline-block text-sm text-blue-600 hover:underline">
        ← 返回列表
      </Link>
      <h2 className="mb-1 text-2xl font-semibold text-zinc-900">{kb.name}</h2>
      <p className="mb-6 text-xs text-zinc-500">
        ID: {kb.id} · 模型: {kb.embedding_model} · {kb.vector_count} 向量
      </p>

      {error && (
        <div className="mb-4 rounded-md bg-red-50 px-4 py-2 text-sm text-red-600">错误：{error}</div>
      )}

      {/* 上传 */}
      <div className="mb-6 rounded-lg border border-dashed border-zinc-300 bg-white p-6 text-center">
        <label className="cursor-pointer">
          <span className="text-sm text-zinc-600">
            {uploading ? '上传中…' : '点击上传 PDF（最大 50MB）'}
          </span>
          <input
            type="file"
            accept="application/pdf"
            onChange={handleUpload}
            disabled={uploading}
            className="hidden"
          />
        </label>
      </div>

      {/* 文档列表 */}
      <h3 className="mb-3 text-sm font-semibold text-zinc-700">文档 ({docs.length})</h3>
      {docs.length === 0 ? (
        <p className="mb-6 text-sm text-zinc-400">还没有文档</p>
      ) : (
        <ul className="mb-6 divide-y divide-zinc-200 rounded-lg border border-zinc-200 bg-white">
          {docs.map((d) => (
            <li key={d.id} className="flex items-center justify-between p-3 text-sm">
              <div>
                <div className="font-medium text-zinc-800">{d.filename}</div>
                <div className="text-xs text-zinc-500">
                  {d.page_count} 页 · {d.status}
                  {d.status === 'failed' && d.error && ` · ${d.error.slice(0, 50)}`}
                </div>
              </div>
              <span
                className={
                  d.status === 'indexed'
                    ? 'text-xs text-green-600'
                    : d.status === 'failed'
                      ? 'text-xs text-red-600'
                      : 'text-xs text-amber-600'
                }
              >
                {d.status}
              </span>
            </li>
          ))}
        </ul>
      )}

      {/* 测试检索 */}
      <h3 className="mb-3 text-sm font-semibold text-zinc-700">测试检索</h3>
      <form onSubmit={handleSearch} className="mb-3 flex gap-2">
        <input
          type="text"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          placeholder="输入问题…"
          className="flex-1 rounded-md border border-zinc-300 px-3 py-2 text-sm outline-none focus:border-blue-400"
        />
        <button
          type="submit"
          disabled={!searchQuery.trim()}
          className="rounded-md bg-blue-500 px-4 py-2 text-sm text-white hover:bg-blue-600 disabled:bg-zinc-300"
        >
          搜索
        </button>
      </form>
      {hits.length > 0 && (
        <ul className="space-y-2">
          {hits.map((h, i) => (
            <li key={i} className="rounded-md border border-zinc-200 bg-white p-3 text-sm">
              <div className="mb-1 text-xs text-zinc-500">
                doc:{h.doc_id.slice(0, 8)} p.{h.page} · score {h.score.toFixed(3)}
              </div>
              <div className="text-zinc-800">{h.text}</div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
