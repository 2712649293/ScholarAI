/** M5.5 · Plan 卡片（在 chat 消息流里渲染，role='plan'）。
 *
 * 四态：
 * - pending  ：三按钮（编辑 / 拒绝 / 批准并开始）
 * - editing  ：textarea 列表 + 保存 / 取消
 * - approved ：整张只读 + ✓ 角标
 * - rejected ：整张灰 + ✗ 角标
 *
 * ponytail：
 * - 编辑保存为一次性 PATCH（onBlur 不发）——避免污染后端状态机
 * - 编辑器本地维护，刷新即丢（可接受）
 */
import { useState } from 'react'
import type { ResearchPlan, PlanStatus } from '@/lib/api'

export interface PlanCardProps {
  plan: ResearchPlan
  status: Exclude<PlanStatus, 'none'>
  onApprove?: () => void
  onReject?: () => void
  onSave?: (patch: Partial<ResearchPlan>) => void
}

export function PlanCard({ plan, status, onApprove, onReject, onSave }: PlanCardProps) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState<ResearchPlan>(plan)

  // 进入编辑态时复制一份草稿
  function enterEdit() {
    setDraft(plan)
    setEditing(true)
  }
  function cancelEdit() {
    setEditing(false)
  }
  function saveEdit() {
    // 只 diff 客户端改了的字段（避免无变更 PATCH）
    const patch: Partial<ResearchPlan> = {}
    if (JSON.stringify(draft.sub_questions) !== JSON.stringify(plan.sub_questions)) {
      patch.sub_questions = draft.sub_questions
    }
    if (JSON.stringify(draft.search_queries) !== JSON.stringify(plan.search_queries)) {
      patch.search_queries = draft.search_queries
    }
    if (JSON.stringify(draft.outline) !== JSON.stringify(plan.outline)) {
      patch.outline = draft.outline
    }
    if (Object.keys(patch).length > 0) onSave?.(patch)
    setEditing(false)
  }

  const isReadOnly = status === 'approved' || status === 'rejected' || status === 'edited'

  return (
    <div
      className={`rounded-lg bg-white px-4 py-3 text-sm shadow-sm ${
        status === 'rejected' ? 'opacity-60' : ''
      }`}
    >
      {/* 头部 */}
      <div className="mb-3 flex items-center justify-between border-b border-zinc-100 pb-2">
        <div className="font-medium text-zinc-800">📋 研究计划：{plan.title}</div>
        <StatusBadge status={status} />
      </div>

      {/* 子问题 */}
      <Section title="子问题">
        {editing ? (
          <EditableList
            items={draft.sub_questions.map((sq) => `${sq.question} — ${sq.rationale}`)}
            onChange={(lines) => {
              const parsed = lines.map((line) => {
                const [q, ...rest] = line.split('—')
                return { question: q.trim(), rationale: rest.join('—').trim() }
              })
              setDraft({ ...draft, sub_questions: parsed })
            }}
            placeholder="子问题 — rationale"
          />
        ) : (
          <ol className="ml-4 list-decimal space-y-1 text-zinc-700">
            {plan.sub_questions.map((sq, i) => (
              <li key={i}>
                <span>{sq.question}</span>
                <span className="ml-2 text-xs text-zinc-400">{sq.rationale}</span>
              </li>
            ))}
          </ol>
        )}
      </Section>

      {/* 检索词 */}
      <Section title="检索词">
        {editing ? (
          <EditableList
            items={draft.search_queries.map(
              (g) => `[${g.intent}] ${g.queries.join(', ')}`,
            )}
            onChange={(lines) => {
              const parsed = lines.map((line) => {
                const m = line.match(/^\[([^\]]*)\]\s*(.*)$/)
                if (!m) return { intent: line, queries: [line] }
                return { intent: m[1].trim(), queries: m[2].split(',').map((s) => s.trim()).filter(Boolean) }
              })
              setDraft({ ...draft, search_queries: parsed })
            }}
            placeholder="[intent] query1, query2"
          />
        ) : (
          <div className="space-y-1">
            {plan.search_queries.map((g, i) => (
              <div key={i} className="text-zinc-700">
                <span className="font-medium">{g.intent}:</span>{' '}
                <span className="font-mono text-xs text-zinc-600">
                  {g.queries.join(', ')}
                </span>
              </div>
            ))}
          </div>
        )}
      </Section>

      {/* 章节大纲 */}
      <Section title="章节大纲">
        {editing ? (
          <EditableList
            items={draft.outline.map((s) => `${s.heading}: ${s.bullets.join('; ')}`)}
            onChange={(lines) => {
              const parsed = lines.map((line) => {
                const idx = line.indexOf(':')
                if (idx < 0) return { heading: line, bullets: [] }
                return {
                  heading: line.slice(0, idx).trim(),
                  bullets: line
                    .slice(idx + 1)
                    .split(';')
                    .map((b) => b.trim())
                    .filter(Boolean),
                }
              })
              setDraft({ ...draft, outline: parsed })
            }}
            placeholder="章节标题: bullet1; bullet2"
          />
        ) : (
          <ol className="ml-4 list-decimal space-y-1 text-zinc-700">
            {plan.outline.map((s, i) => (
              <li key={i}>
                <div className="font-medium">{s.heading}</div>
                <ul className="ml-4 list-disc text-xs text-zinc-600">
                  {s.bullets.map((b, j) => (
                    <li key={j}>{b}</li>
                  ))}
                </ul>
              </li>
            ))}
          </ol>
        )}
      </Section>

      {/* reasoning */}
      {plan.reasoning && (
        <div className="mt-3 text-xs italic text-zinc-500">{plan.reasoning}</div>
      )}

      {/* 按钮区 */}
      <div className="mt-4 flex justify-end gap-2 border-t border-zinc-100 pt-3">
        {editing ? (
          <>
            <button
              onClick={cancelEdit}
              className="rounded border border-zinc-300 px-3 py-1 text-xs text-zinc-600 hover:bg-zinc-50"
            >
              取消
            </button>
            <button
              onClick={saveEdit}
              className="rounded bg-blue-500 px-3 py-1 text-xs text-white hover:bg-blue-600"
            >
              保存编辑
            </button>
          </>
        ) : isReadOnly ? (
          <span className="text-xs text-zinc-400">
            {status === 'approved' && '已批准，研究流执行中'}
            {status === 'rejected' && '已拒绝，重新输入方向可生成新计划'}
            {status === 'edited' && '已编辑，等待批准'}
          </span>
        ) : (
          <>
            {onSave && (
              <button
                onClick={enterEdit}
                className="rounded border border-zinc-300 px-3 py-1 text-xs text-zinc-600 hover:bg-zinc-50"
              >
                ✏️ 编辑
              </button>
            )}
            {onReject && (
              <button
                onClick={onReject}
                className="rounded border border-red-200 px-3 py-1 text-xs text-red-600 hover:bg-red-50"
              >
                ❌ 拒绝
              </button>
            )}
            {onApprove && (
              <button
                onClick={onApprove}
                className="rounded bg-blue-500 px-3 py-1 text-xs text-white hover:bg-blue-600"
              >
                ✅ 批准并开始
              </button>
            )}
          </>
        )}
      </div>
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mb-2">
      <div className="mb-1 text-xs font-medium uppercase text-zinc-500">{title}</div>
      {children}
    </div>
  )
}

function StatusBadge({ status }: { status: PlanStatus }) {
  const map: Record<PlanStatus, { text: string; cls: string }> = {
    none: { text: '—', cls: 'text-zinc-400' },
    pending: { text: '待审', cls: 'text-amber-600 bg-amber-50' },
    edited: { text: '已编辑', cls: 'text-blue-600 bg-blue-50' },
    approved: { text: '✓ 已批准', cls: 'text-green-600 bg-green-50' },
    rejected: { text: '✗ 已拒绝', cls: 'text-red-600 bg-red-50' },
  }
  const { text, cls } = map[status]
  return (
    <span className={`rounded px-2 py-0.5 text-xs ${cls}`}>{text}</span>
  )
}

/** 简易 textarea 列表编辑器：每行一项，右上角 × 删除，底部 + 添加。 */
function EditableList({
  items,
  onChange,
  placeholder,
}: {
  items: string[]
  onChange: (lines: string[]) => void
  placeholder?: string
}) {
  function setLine(i: number, val: string) {
    const next = [...items]
    next[i] = val
    onChange(next)
  }
  function delLine(i: number) {
    onChange(items.filter((_, idx) => idx !== i))
  }
  function addLine() {
    onChange([...items, ''])
  }
  return (
    <div className="space-y-1">
      {items.map((line, i) => (
        <div key={i} className="flex items-start gap-1">
          <textarea
            value={line}
            onChange={(e) => setLine(i, e.target.value)}
            placeholder={placeholder}
            rows={1}
            className="flex-1 resize-y rounded border border-zinc-200 px-2 py-1 text-xs outline-none focus:border-blue-400"
          />
          <button
            type="button"
            onClick={() => delLine(i)}
            className="px-1 text-xs text-zinc-400 hover:text-red-500"
            title="删除"
          >
            ×
          </button>
        </div>
      ))}
      <button
        type="button"
        onClick={addLine}
        className="text-xs text-blue-500 hover:text-blue-700"
      >
        + 添加
      </button>
    </div>
  )
}