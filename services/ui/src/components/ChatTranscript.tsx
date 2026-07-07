import { useState } from 'react'
import {
  GitBranch,
  Brain,
  Library,
  Database,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  AlertCircle,
  Check,
  Copy,
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
  databases: { label: 'Databases', icon: Database, color: '#d97706' },
}

// Some models (DeepSeek reasoner) occasionally leak inline tool-call markup
// (<|DSML|>invoke ...>) into their text/reasoning stream. The backend recovers
// the calls; here we just keep the junk out of what the user reads.
const DSML_RE = /<\/?\|DSML\|>[^<]*|<\|[^|>]*\|>/g

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

export function ToolCallCard({ call }: { call: ChatToolCall }) {
  const [open, setOpen] = useState(false)
  const meta = SOURCE_META[call.source]
  const Icon = meta.icon
  return (
    <div style={{ border: '1px solid var(--border)' }} className="rounded bg-surface">
      <button
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
            call.results.map((r, idx) => (
              <div key={idx} className="text-xs">
                <p className="font-mono text-muted mb-0.5">
                  {resultTitle(call.source, r)}
                  {typeof r.score === 'number' && (
                    <span className="ml-2">score {Number(r.score).toFixed(3)}</span>
                  )}
                </p>
                <p className="text-text">{resultBody(call.source, r)}</p>
              </div>
            ))
          )}
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
export function Transcript({ turns }: { turns: StoredChatTurn[] }) {
  return (
    <div className="space-y-5">
      {turns.map((turn, idx) =>
        turn.role === 'user' ? (
          <UserBubble key={idx} content={turn.content} />
        ) : (
          <div key={idx} className="space-y-2">
            {(turn.toolCalls?.length ?? 0) > 0 && (
              <div className="space-y-1.5">
                <SourceBadges calls={turn.toolCalls!} />
                {turn.toolCalls!.map((call, ci) => (
                  <ToolCallCard key={ci} call={call} />
                ))}
              </div>
            )}
            {turn.reasoning && <ReasoningBlock text={turn.reasoning} />}
            {turn.content && <AssistantBody content={turn.content} />}
            {(turn.content || turn.model) && (
              <div className="flex items-center gap-2 text-[11px] text-muted px-0.5">
                {turn.content && <CopyButton text={turn.content} />}
                {turn.model && <span>via {turn.model}</span>}
                {turn.stopped && <span>· stopped</span>}
              </div>
            )}
          </div>
        )
      )}
    </div>
  )
}
