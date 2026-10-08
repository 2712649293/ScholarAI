import { useEffect, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { createKB, deleteKB, listKBs, type KB, ApiError } from '@/lib/api'

export function KnowledgePage() {
  const [kbs, setKbs] = useState<KB[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [newName, setNewName] = useState('')
  const [creating, setCreating] = useState(false)

  async function refresh() {
    try {
      setKbs(await listKBs())
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    refresh()
  }, [])

  async function handleCreate(e: FormEvent) {
    e.preventDefault()
    if (!newName.trim()) return
    setCreating(true)
    setError(null)
    try {
      await createKB(newName.trim())
      setNewName('')
      await refresh()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setCreating(false)
    }
  }

  async function handleDelete(id: string, name: string) {
    if (!confirm(`删除知识库「${name}」？该操作不可恢复，会同时清空向量库。`)) return
    try {
      await deleteKB(id)
      await refresh()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    }
  }

  return (
    <div className="mx-auto max-w-4xl p-6">
      <h2 className="mb-4 text-2xl font-semibold text-zinc-900">知识库</h2>

      <form onSubmit={handleCreate} className="mb-6 flex gap-2">
        <input
          type="text"
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
          placeholder="新知识库名称…"
          className="flex-1 rounded-md border border-zinc-300 px-3 py-2 text-sm outline-none focus:border-blue-400"
          disabled={creating}
        />
        <button
          type="submit"
          disabled={!newName.trim() || creating}
          className="rounded-md bg-blue-500 px-4 py-2 text-sm font-medium text-white hover:bg-blue-600 disabled:cursor-not-allowed disabled:bg-zinc-300"
        >
          {creating ? '创建中…' : '创建'}
        </button>
      </form>

      {error && (
        <div className="mb-4 rounded-md bg-red-50 px-4 py-2 text-sm text-red-600">错误：{error}</div>
      )}

      {loading ? (
        <p className="text-sm text-zinc-500">加载中…</p>
      ) : kbs.length === 0 ? (
        <p className="text-sm text-zinc-400">还没有知识库。在上方输入名称开始创建。</p>
      ) : (
        <ul className="divide-y divide-zinc-200 rounded-lg border border-zinc-200 bg-white">
          {kbs.map((kb) => (
            <li key={kb.id} className="flex items-center justify-between p-4 hover:bg-zinc-50">
              <Link to={`/knowledge/${kb.id}`} className="flex-1">
                <div className="font-medium text-zinc-900">{kb.name}</div>
                <div className="mt-1 text-xs text-zinc-500">
                  {kb.doc_count} 文档 · {kb.vector_count} 向量 · {kb.embedding_model}
                </div>
              </Link>
              <button
                onClick={() => handleDelete(kb.id, kb.name)}
                className="rounded-md px-3 py-1 text-xs text-red-600 hover:bg-red-50"
              >
                删除
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
