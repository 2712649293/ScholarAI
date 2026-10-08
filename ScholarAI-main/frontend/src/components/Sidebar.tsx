import { useEffect, useState } from 'react'
import { NavLink, useLocation, useNavigate } from 'react-router-dom'
import { deleteSession, listSessions, type SessionSummary } from '@/lib/api'

function relTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime()
  const m = Math.floor(diff / 60000)
  if (m < 1) return '刚刚'
  if (m < 60) return `${m} 分钟前`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h} 小时前`
  return `${Math.floor(h / 24)} 天前`
}

export function Sidebar() {
  const navigate = useNavigate()
  const location = useLocation()
  const [sessions, setSessions] = useState<SessionSummary[]>([])
  const currentId = location.pathname.startsWith('/chat/')
    ? location.pathname.slice('/chat/'.length)
    : undefined

  // 路由变化时刷新列表（新对话产生 / 切换会话后 title、排序更新）
  useEffect(() => {
    listSessions().then(setSessions).catch(() => setSessions([]))
  }, [location.pathname])

  async function handleDelete(e: React.MouseEvent, id: string) {
    e.preventDefault()
    e.stopPropagation()
    await deleteSession(id).catch(() => {})
    setSessions((s) => s.filter((x) => x.id !== id))
    if (id === currentId) navigate('/chat')
  }

  return (
    <aside className="flex h-full w-56 flex-col border-r border-zinc-200 bg-white">
      {/* 顶部：Logo / 新对话 */}
      <div className="border-b border-zinc-200 p-3">
        <h1 className="mb-2 text-lg font-semibold text-zinc-900">ScholarAI</h1>
        <button
          onClick={() => navigate('/chat')}
          className="w-full rounded-md border border-zinc-300 bg-white px-3 py-1.5 text-left text-sm text-zinc-700 transition-colors hover:bg-zinc-50"
        >
          + 新对话
        </button>
      </div>

      {/* 中部：导航 + 会话列表 */}
      <nav className="flex-1 space-y-1 overflow-y-auto p-2 text-sm">
        <NavItem to="/knowledge" label="知识库" />

        <div className="px-3 pb-1 pt-3 text-xs font-medium uppercase tracking-wide text-zinc-400">
          对话
        </div>
        {sessions.length === 0 ? (
          <p className="px-3 py-1 text-xs text-zinc-400">还没有对话</p>
        ) : (
          sessions.map((s) => (
            <button
              key={s.id}
              onClick={() => navigate(`/chat/${s.id}`)}
              className={`group flex w-full items-center gap-1 rounded-md px-3 py-2 text-left transition-colors ${
                s.id === currentId
                  ? 'bg-blue-50 text-blue-600'
                  : 'text-zinc-700 hover:bg-zinc-50'
              }`}
            >
              <span className="min-w-0 flex-1">
                <span className="flex items-center gap-1">
                  <span className="truncate">{s.title || '新对话'}</span>
                  {s.mode === 'research' && (
                    <span className="shrink-0 rounded bg-purple-100 px-1 text-[10px] text-purple-600">
                      研究
                    </span>
                  )}
                </span>
                <span className="block text-xs text-zinc-400">{relTime(s.updated_at)}</span>
              </span>
              <span
                role="button"
                aria-label="删除对话"
                onClick={(e) => handleDelete(e, s.id)}
                className="hidden shrink-0 rounded px-1 text-zinc-400 hover:text-red-500 group-hover:inline"
              >
                🗑
              </span>
            </button>
          ))
        )}
      </nav>

      {/* 底部：状态 */}
      <div className="border-t border-zinc-200 p-3 text-xs text-zinc-400">v0.1.0-m2</div>
    </aside>
  )
}

function NavItem({ to, label }: { to: string; label: string }) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) =>
        `block rounded-md px-3 py-2 transition-colors ${
          isActive ? 'bg-blue-50 font-medium text-blue-600' : 'text-zinc-700 hover:bg-zinc-50'
        }`
      }
    >
      {label}
    </NavLink>
  )
}
