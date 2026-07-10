import { useEffect } from 'react'
import { ExternalLink, X } from 'lucide-react'
import type { CitationSource } from './ChatTranscript'
import { SOURCE_META } from './ChatTranscript'

function str(v: unknown): string {
  if (v == null) return ''
  return typeof v === 'string' ? v : JSON.stringify(v, null, 2)
}

/** A scrollable block of raw text/code from a source chunk. */
function TextBlock({ text }: { text: string }) {
  return (
    <pre
      className="text-xs whitespace-pre-wrap break-words rounded p-3 overflow-x-auto scrollbar-thin"
      style={{ background: 'var(--code-bg)', border: '1px solid var(--border)' }}
    >
      {text}
    </pre>
  )
}

/** Render a rows/columns result (a query_database result) as a small table. */
function RowsTable({
  columns,
  rows,
}: {
  columns: string[]
  rows: Record<string, unknown>[]
}) {
  return (
    <div className="overflow-x-auto scrollbar-thin" style={{ border: '1px solid var(--border)' }}>
      <table className="text-xs border-collapse w-full">
        <thead>
          <tr>
            {columns.map((c) => (
              <th
                key={c}
                className="text-left font-semibold text-muted px-2 py-1 whitespace-nowrap"
                style={{ borderBottom: '1px solid var(--border)' }}
              >
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} style={{ borderBottom: '1px solid var(--border)' }}>
              {columns.map((c) => (
                <td key={c} className="px-2 py-1 align-top font-mono break-words max-w-[240px]">
                  {str(row[c])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** Key/value view for arbitrary object results (schema/table descriptions). */
function KeyValues({ obj }: { obj: Record<string, unknown> }) {
  const entries = Object.entries(obj).filter(([, v]) => v != null && v !== '')
  return (
    <dl className="text-xs space-y-1.5">
      {entries.map(([k, v]) => (
        <div key={k}>
          <dt className="text-muted font-medium">{k}</dt>
          <dd className="mt-0.5">
            {typeof v === 'object' ? <TextBlock text={str(v)} /> : <span className="break-words">{str(v)}</span>}
          </dd>
        </div>
      ))}
    </dl>
  )
}

function SourceBody({ source }: { source: CitationSource }) {
  const r = source.result
  if (source.source === 'memory') {
    return <TextBlock text={str(r.memory ?? r.content ?? r)} />
  }
  if (source.source === 'web') {
    const url = typeof r.url === 'string' ? r.url : null
    return (
      <div className="space-y-2">
        {url && (
          <a
            href={url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1.5 text-xs text-accent underline break-all"
          >
            <ExternalLink className="w-3 h-3 flex-shrink-0" />
            {url}
          </a>
        )}
        <TextBlock text={str(r.content ?? r)} />
      </div>
    )
  }
  if (source.source === 'databases') {
    // query_database returns columns + rows; schema/table results are objects.
    const columns = Array.isArray(r.columns) ? (r.columns as unknown[]) : null
    const rows = Array.isArray(r.rows) ? (r.rows as Record<string, unknown>[]) : null
    if (columns && rows && columns.every((c) => typeof c === 'string')) {
      return (
        <div className="space-y-2">
          {typeof r.sql === 'string' && <TextBlock text={str(r.sql)} />}
          <RowsTable columns={columns as string[]} rows={rows} />
        </div>
      )
    }
    return <KeyValues obj={r} />
  }
  // repositories & knowledge_base both carry a `content` chunk.
  return <TextBlock text={str(r.content ?? r)} />
}

/**
 * Right-hand drawer that shows a single retrieved source excerpt in full —
 * a code chunk, document passage, memory, or database rows — opened by clicking
 * a citation reference in the chat.
 */
export function SourcePanel({
  source,
  onClose,
}: {
  source: CitationSource | null
  onClose: () => void
}) {
  useEffect(() => {
    if (!source) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [source, onClose])

  if (!source) return null
  const meta = SOURCE_META[source.source]
  const Icon = meta.icon
  const r = source.result
  const score = typeof r.score === 'number' ? Number(r.score).toFixed(3) : null

  return (
    <>
      {/* Backdrop — click to dismiss (semi-transparent on mobile, invisible on desktop). */}
      <div
        className="fixed inset-0 z-40 bg-black/30 md:bg-transparent md:pointer-events-none"
        onClick={onClose}
        aria-hidden="true"
      />
      <aside
        style={{ borderLeft: '1px solid var(--border)', width: 'min(440px, 100%)' }}
        className="fixed top-0 right-0 h-full z-50 bg-bg flex flex-col shadow-xl"
        role="dialog"
        aria-label="Source excerpt"
      >
        <div
          style={{ borderBottom: '1px solid var(--border)' }}
          className="px-4 py-3 flex items-start justify-between gap-3 flex-shrink-0"
        >
          <div className="min-w-0">
            <div className="flex items-center gap-1.5">
              <span className="text-[11px] text-muted">[{source.n}]</span>
              <Icon className="w-3.5 h-3.5 flex-shrink-0" style={{ color: meta.color }} />
              <span className="text-xs font-medium" style={{ color: meta.color }}>
                {meta.label}
              </span>
              {score && <span className="text-[11px] text-muted">· score {score}</span>}
            </div>
            <p className="text-sm font-medium text-text mt-1 break-words">{source.title}</p>
            <p className="text-[11px] text-muted mt-0.5 break-words">from “{source.query}”</p>
          </div>
          <button
            onClick={onClose}
            className="text-muted hover:text-text transition-colors p-1 flex-shrink-0"
            aria-label="Close"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto scrollbar-thin px-4 py-3">
          <SourceBody source={source} />
        </div>
      </aside>
    </>
  )
}
