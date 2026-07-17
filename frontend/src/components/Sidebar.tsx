import { NavLink, useNavigate } from 'react-router-dom'

export function Sidebar() {
  const navigate = useNavigate()

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

      {/* 中部：导航 */}
      <nav className="flex-1 space-y-1 p-2 text-sm">
        <NavItem to="/chat" label="对话" />
        <NavItem to="/knowledge" label="知识库" />
      </nav>

      {/* 底部：状态 */}
      <div className="border-t border-zinc-200 p-3 text-xs text-zinc-400">
        v0.1.0-m2
      </div>
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
