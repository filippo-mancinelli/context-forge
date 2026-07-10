import { useState } from 'react'
import {
  GitBranch,
  Brain,
  Library,
  Globe,
  Database,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  AlertCircle,
  Check,
  Copy,
  ExternalLink,
  Sparkles,
} from 'lucide-react'
import type { ChatToolCall, StoredChatTurn } from '../lib/api'
import { Markdown } from './Markdown'
import { Spinner } from './ui/Spinner'

export const SOURCE_META: Record<
  ChatToolCall['source'],
  { label: string; icon: typeof GitBranch; color: string }
> = {
  repositories: { label: 'Repositories', icon: GitBranch, color: '#2563eb' },
  memory: { label: 'Memory', icon: Brain, color: '#7c3aed' },
  knowledge_base: { label: 'Knowledge Base', icon: Library, color: '#0d9488' },
  web: { label: 'Web', icon: Globe, color: '#c026d3' },
  databases: { label: 'Databases', icon: Database, color: '#d97706' },
}

// Some models (DeepSeek reasoner) occasionally leak inline tool-call markup
// (<｜DSML｜>invoke ...>) into their text/reasoning stream. The backend recovers
// the calls; here we just keep the junk out of what the user reads. DeepSeek's
// tokens use the fullwidth vertical bar U+FF5C ("｜"), not ASCII "|" — match both.
const DSML_RE = /<\/?[|｜]DSML[|｜]>[^<]*|<[|｜][^|｜>]*[|｜]>/g

export function stripDsml(text: string): string {
  return text.replace(DSML_RE, '')
}

function snippet(value: unknown, max = 240): string {
  const text = typeof value === 'string' ? value : JSON.stringify(value)
  const flat = text.replace(/\s+/g, ' ').trim()
  return flat.length > max ? `${flat.slice(0, max)}…` : flat
}

function resultTitle(source: ChatToolCall['source'], r: Record<string, unknown>): string {
  if (source === 'repositories') {
    return `${r.repo_name ?? '?'} · ${r.file_path ?? '?'}`
  }
  if (source === 'knowledge_base') {
    return String(r.title ?? r.filename ?? `document ${r.document_id ?? ''}`)
  }
  if (source === 'web') {
    return String(r.title || r.url || `page ${r.page_id ?? ''}`)
  }
  if (source === 'databases') {
    return String(r.connection ?? r.table ?? 'database')
  }
  return 'memory'
}

function resultBody(source: ChatToolCall['source'], r: Record<string, unknown>): string {
  if (source === 'memory') return snippet(r.memory)
  if (source === 'databases') return snippet(r.rows ?? r.columns ?? r.description ?? r)
  return snippet(r.content)
}

/**
 * A single retrieved chunk, promoted to a citable "source" the user can open
 * in the side panel. Numbered per assistant turn ([1], [2], …) so the answer
 * and its supporting excerpts line up like footnotes.
 */
export interface CitationSource {
  n: number
  source: ChatToolCall['source']
  title: string
  query: string
  result: Record<string, unknown>
}

/** Flatten a turn's tool calls into an ordered, numbered list of sources. */
export function collectSources(calls: ChatToolCall[]): CitationSource[] {
  const out: CitationSource[] = []
  for (const call of calls) {
    if (call.error) continue
    for (const r of call.results) {
      out.push({
        n: out.length + 1,
        source: call.source,
        title: resultTitle(call.source, r),
        query: call.query,
        result: r,
      })
    }
  }
  return out
}

export function ToolCallCard({
  call,
  sources,
  onOpenSource,
}: {
  call: ChatToolCall
  sources?: CitationSource[]
  onOpenSource?: (s: CitationSource) => void
}) {
  const [open, setOpen] = useState(false)
  const meta = SOURCE_META[call.source]
  const Icon = meta.icon
  return (
    <div style={{ border: '1px solid var(--border)' }} className="rounded bg-surface">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center gap-2 px-3 py-2 text-left"
      >
        {open ? (
          <ChevronDown className="w-3.5 h-3.5 text-muted flex-shrink-0" />
        ) : (
          <ChevronRight className="w-3.5 h-3.5 text-muted flex-shrink-0" />
        )}
        <Icon className="w-3.5 h-3.5 flex-shrink-0" style={{ color: meta.color }} />
        <span className="text-xs font-medium" style={{ color: meta.color }}>
          {meta.label}
        </span>
        <span className="text-xs text-muted truncate flex-1">“{call.query}”</span>
        {call.error ? (
          <span className="text-xs text-[var(--danger)] flex items-center gap-1">
            <AlertCircle className="w-3 h-3" /> error
          </span>
        ) : (
          <span className="text-xs text-muted flex-shrink-0">
            {call.result_count} {call.result_count === 1 ? 'result' : 'results'}
          </span>
        )}
      </button>
      {open && (
        <div style={{ borderTop: '1px solid var(--border)' }} className="px-3 py-2 space-y-2">
          {call.error ? (
            <p className="text-xs text-[var(--danger)]">{call.error}</p>
          ) : call.results.length === 0 ? (
            <p className="text-xs text-muted">No matches returned.</p>
          ) : (
            call.results.map((r, idx) => {
              const src = sources?.find((s) => s.result === r)
              const clickable = !!(onOpenSource && src)
              const inner = (
                <>
                  <p className="font-mono text-muted mb-0.5 flex items-center gap-1.5">
                    {src && <span className="text-[10px] flex-shrink-0">[{src.n}]</span>}
                    <span className={clickable ? 'group-hover:underline' : ''}>
                      {resultTitle(call.source, r)}
                    </span>
                    {typeof r.score === 'number' && (
                      <span className="ml-1 flex-shrink-0">score {Number(r.score).toFixed(3)}</span>
                    )}
                    {clickable && (
                      <ExternalLink className="w-3 h-3 flex-shrink-0 opacity-0 group-hover:opacity-100 text-accent" />
                    )}
                  </p>
                  <p className="text-text">{resultBody(call.source, r)}</p>
                </>
              )
              return clickable ? (
                <button
                  key={idx}
                  type="button"
                  onClick={() => onOpenSource!(src!)}
                  className="group block w-full text-left text-xs rounded px-1 -mx-1 py-0.5 hover:bg-[color:var(--code-bg)] transition-colors"
                  title="Open source excerpt"
                >
                  {inner}
                </button>
              ) : (
                <div key={idx} className="text-xs">
                  {inner}
                </div>
              )
            })
          )}
        </div>
      )}
    </div>
  )
}

// Display order of source groups in the references section.
const SOURCE_GROUP_ORDER: ChatToolCall['source'][] = [
  'repositories',
  'web',
  'knowledge_base',
  'memory',
  'databases',
]

// How many chips to show per group before a "+N more" toggle.
const GROUP_CHIP_CAP = 12

function SourceChip({
  source,
  onOpen,
}: {
  source: CitationSource
  onOpen: (s: CitationSource) => void
}) {
  const meta = SOURCE_META[source.source]
  const Icon = meta.icon
  return (
    <button
      type="button"
      onClick={() => onOpen(source)}
      title={source.title}
      style={{ border: '1px solid var(--border)' }}
      className="group inline-flex items-center gap-1 text-[11px] pl-1 pr-1.5 py-0.5 rounded hover:border-[var(--accent)] hover:bg-surface transition-colors max-w-[240px]"
    >
      <span className="text-muted flex-shrink-0">[{source.n}]</span>
      <Icon className="w-3 h-3 flex-shrink-0" style={{ color: meta.color }} />
      <span className="truncate text-text group-hover:text-accent">{source.title}</span>
    </button>
  )
}

/**
 * Collapsible, grouped citation footnotes under an assistant answer. Collapsed
 * by default (a broad query can pull 100+ chunks — e.g. a whole DB schema), it
 * shows a per-source summary; expanding reveals the chips grouped by source,
 * each opening the source excerpt in the side panel (never navigating away).
 */
export function SourceReferences({
  sources,
  onOpen,
}: {
  sources: CitationSource[]
  onOpen: (s: CitationSource) => void
}) {
  const [expanded, setExpanded] = useState(false)
  const [showAll, setShowAll] = useState<Set<ChatToolCall['source']>>(new Set())

  if (sources.length === 0) return null

  const groups = SOURCE_GROUP_ORDER.map((source) => ({
    source,
    items: sources.filter((s) => s.source === source),
  })).filter((g) => g.items.length > 0)

  return (
    <div className="pt-0.5">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex items-center gap-2 flex-wrap text-[11px] text-muted hover:text-text transition-colors"
      >
        {expanded ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
        <span>Sources ({sources.length})</span>
        {!expanded &&
          groups.map((g) => {
            const meta = SOURCE_META[g.source]
            const Icon = meta.icon
            return (
              <span key={g.source} className="inline-flex items-center gap-1" title={meta.label}>
                <Icon className="w-3 h-3" style={{ color: meta.color }} />
                {g.items.length}
              </span>
            )
          })}
      </button>

      {expanded && (
        <div className="mt-2 space-y-2.5">
          {groups.map((g) => {
            const meta = SOURCE_META[g.source]
            const all = showAll.has(g.source)
            const shown = all ? g.items : g.items.slice(0, GROUP_CHIP_CAP)
            const hidden = g.items.length - shown.length
            return (
              <div key={g.source}>
                <div
                  className="text-[10px] uppercase tracking-wider mb-1"
                  style={{ color: meta.color }}
                >
                  {meta.label} ({g.items.length})
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {shown.map((s) => (
                    <SourceChip key={s.n} source={s} onOpen={onOpen} />
                  ))}
                  {hidden > 0 && (
                    <button
                      type="button"
                      onClick={() =>
                        setShowAll((prev) => new Set(prev).add(g.source))
                      }
                      className="text-[11px] text-muted hover:text-accent underline px-1 py-0.5"
                    >
                      +{hidden} more
                    </button>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

export function PendingToolCard({
  pending,
}: {
  pending: Pick<ChatToolCall, 'tool' | 'source' | 'query'>
}) {
  const meta = SOURCE_META[pending.source]
  const Icon = meta.icon
  return (
    <div
      style={{ border: '1px solid var(--border)' }}
      className="rounded bg-surface flex items-center gap-2 px-3 py-2"
    >
      <Spinner size={13} className="text-muted" />
      <Icon className="w-3.5 h-3.5 flex-shrink-0" style={{ color: meta.color }} />
      <span className="text-xs font-medium" style={{ color: meta.color }}>
        {meta.label}
      </span>
      <span className="text-xs text-muted truncate flex-1">“{pending.query}”</span>
      <span className="text-xs text-muted flex-shrink-0">searching…</span>
    </div>
  )
}

export function SourceBadges({ calls }: { calls: ChatToolCall[] }) {
  const used = new Set(calls.filter((c) => !c.error && c.result_count > 0).map((c) => c.source))
  return (
    <div className="flex items-center gap-2 flex-wrap">
      {(Object.keys(SOURCE_META) as ChatToolCall['source'][]).map((source) => {
        const meta = SOURCE_META[source]
        const Icon = meta.icon
        const active = used.has(source)
        return (
          <span
            key={source}
            className="inline-flex items-center gap-1 text-xs px-1.5 py-0.5 rounded"
            style={{
              color: active ? meta.color : 'var(--muted)',
              border: `1px solid ${active ? meta.color : 'var(--border)'}`,
              opacity: active ? 1 : 0.5,
            }}
            title={active ? `${meta.label} returned results` : `${meta.label} not used`}
          >
            <Icon className="w-3 h-3" />
            {meta.label}
          </span>
        )
      })}
    </div>
  )
}

export function ReasoningBlock({
  text,
  streaming = false,
  hasAnswer = true,
}: {
  text: string
  streaming?: boolean
  hasAnswer?: boolean
}) {
  const [userOpen, setUserOpen] = useState<boolean | null>(null)
  // Auto-expand while the model is only reasoning; collapse once the answer starts.
  const open = userOpen ?? (streaming && !hasAnswer)
  const active = streaming && !hasAnswer
  const cleaned = stripDsml(text)
  if (!cleaned.trim()) return null
  return (
    <div>
      <button
        onClick={() => setUserOpen(!open)}
        className="flex items-center gap-1.5 text-xs text-muted hover:text-text transition-colors"
      >
        <Sparkles className={`w-3 h-3 ${active ? 'animate-pulse text-accent' : ''}`} />
        {active ? 'Reasoning…' : 'Reasoning'}
        {open ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
      </button>
      {open && (
        <div
          style={{ borderLeft: '2px solid var(--border)' }}
          className="mt-1.5 ml-1 pl-3 text-xs text-muted whitespace-pre-wrap break-words leading-relaxed max-h-56 overflow-y-auto scrollbar-thin"
        >
          {cleaned}
        </div>
      )}
    </div>
  )
}

export function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  const copy = () => {
    void navigator.clipboard.writeText(text).then(() => {
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    })
  }
  return (
    <button
      onClick={copy}
      className="text-muted hover:text-text transition-colors p-0.5"
      title="Copy response"
      aria-label="Copy response"
    >
      {copied ? <Check className="w-3.5 h-3.5 text-success" /> : <Copy className="w-3.5 h-3.5" />}
    </button>
  )
}

export function UserBubble({ content }: { content: string }) {
  return (
    <div className="flex justify-end">
      <div
        className="max-w-[85%] sm:max-w-[80%] rounded px-3 py-2 text-sm bg-[#eaf4fb] whitespace-pre-wrap break-words"
        style={{ border: '1px solid var(--border)' }}
      >
        {content}
      </div>
    </div>
  )
}

export function AssistantBody({
  content,
  streaming = false,
}: {
  content: string
  streaming?: boolean
}) {
  return (
    <div
      className="rounded px-3 py-2 text-sm bg-surface overflow-hidden"
      style={{ border: '1px solid var(--border)' }}
    >
      <Markdown content={stripDsml(content)} />
      {streaming && (
        <span className="inline-block w-1.5 h-3.5 bg-accent animate-pulse rounded-sm mt-1" />
      )}
    </div>
  )
}

/** Read-only rendering of a saved/shared conversation. */
export function Transcript({
  turns,
  onOpenSource,
}: {
  turns: StoredChatTurn[]
  onOpenSource?: (s: CitationSource) => void
}) {
  return (
    <div className="space-y-5">
      {turns.map((turn, idx) => {
        if (turn.role === 'user') return <UserBubble key={idx} content={turn.content} />
        const sources = onOpenSource ? collectSources(turn.toolCalls ?? []) : []
        return (
          <div key={idx} className="space-y-2">
            {(turn.toolCalls?.length ?? 0) > 0 && (
              <div className="space-y-1.5">
                <SourceBadges calls={turn.toolCalls!} />
                {turn.toolCalls!.map((call, ci) => (
                  <ToolCallCard key={ci} call={call} sources={sources} onOpenSource={onOpenSource} />
                ))}
              </div>
            )}
            {turn.reasoning && <ReasoningBlock text={turn.reasoning} />}
            {turn.content && <AssistantBody content={turn.content} />}
            {onOpenSource && sources.length > 0 && (
              <SourceReferences sources={sources} onOpen={onOpenSource} />
            )}
            {(turn.content || turn.model) && (
              <div className="flex items-center gap-2 text-[11px] text-muted px-0.5">
                {turn.content && <CopyButton text={turn.content} />}
                {turn.model && <span>via {turn.model}</span>}
                {turn.stopped && <span>· stopped</span>}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
