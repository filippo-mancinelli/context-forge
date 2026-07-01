import { useEffect, useRef, useState, type FormEvent } from 'react'
import { GitBranch, Brain, Library, ChevronDown, ChevronRight, AlertCircle } from 'lucide-react'
import { api, type ChatMessage, type ChatToolCall } from '../lib/api'
import { Button, Textarea } from '../components/ui'
import { Markdown } from '../components/Markdown'

interface AssistantTurn {
  role: 'assistant'
  content: string
  toolCalls: ChatToolCall[]
  model?: string
}
interface UserTurn {
  role: 'user'
  content: string
}
type Turn = UserTurn | AssistantTurn

const SOURCE_META: Record<
  ChatToolCall['source'],
  { label: string; icon: typeof GitBranch; color: string }
> = {
  repositories: { label: 'Repositories', icon: GitBranch, color: '#2563eb' },
  memory: { label: 'Memory', icon: Brain, color: '#7c3aed' },
  knowledge_base: { label: 'Knowledge Base', icon: Library, color: '#0d9488' },
}

const SUGGESTIONS = [
  'What repositories are indexed and what do they do?',
  'What do you remember about my preferences or past decisions?',
  'Summarize what the uploaded documents cover.',
]

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
  return 'memory'
}

function resultBody(source: ChatToolCall['source'], r: Record<string, unknown>): string {
  if (source === 'memory') return snippet(r.memory)
  return snippet(r.content)
}

function ToolCallCard({ call }: { call: ChatToolCall }) {
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

function SourceBadges({ calls }: { calls: ChatToolCall[] }) {
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

export default function Chat() {
  const [turns, setTurns] = useState<Turn[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [turns, loading])

  const send = async (text: string) => {
    const trimmed = text.trim()
    if (!trimmed || loading) return
    setError(null)
    const nextTurns: Turn[] = [...turns, { role: 'user', content: trimmed }]
    setTurns(nextTurns)
    setInput('')
    setLoading(true)

    // Build API history from the visible conversation.
    const history: ChatMessage[] = nextTurns.map((t) => ({ role: t.role, content: t.content }))
    try {
      const res = await api.chat.send(history)
      setTurns((prev) => [
        ...prev,
        { role: 'assistant', content: res.reply, toolCalls: res.tool_calls, model: res.model },
      ])
    } catch (e) {
      setError(String(e))
      // Drop the optimistic user turn's pending state by leaving it; surface error separately.
    } finally {
      setLoading(false)
    }
  }

  const onSubmit = (e: FormEvent) => {
    e.preventDefault()
    void send(input)
  }

  return (
    <div className="flex flex-col h-full">
      <div className="p-4 sm:p-8 pb-3">
        <div className="page-content">
          <div className="flex items-start justify-between gap-4">
            <div>
              <h1>Agent Chat</h1>
              <p className="text-muted text-sm">
                Chat with a retrieval agent to verify it pulls from your knowledge base, memory,
                and indexed repositories. Every search it runs is shown inline.
              </p>
            </div>
            {turns.length > 0 && (
              <Button variant="secondary" onClick={() => setTurns([])} disabled={loading}>
                New chat
              </Button>
            )}
          </div>
        </div>
      </div>

      <div ref={scrollRef} className="flex-1 overflow-auto px-4 sm:px-8">
        <div className="page-content space-y-4 pb-4">
          {turns.length === 0 && (
            <div style={{ border: '1px dashed var(--border)' }} className="rounded p-4 space-y-2">
              <p className="text-sm text-muted">Try asking:</p>
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  onClick={() => void send(s)}
                  disabled={loading}
                  className="block text-left text-sm text-accent hover:underline"
                >
                  {s}
                </button>
              ))}
            </div>
          )}

          {turns.map((turn, idx) =>
            turn.role === 'user' ? (
              <div key={idx} className="flex justify-end">
                <div
                  className="max-w-[85%] sm:max-w-[80%] rounded px-3 py-2 text-sm bg-[#eaf4fb] whitespace-pre-wrap break-words"
                  style={{ border: '1px solid var(--border)' }}
                >
                  {turn.content}
                </div>
              </div>
            ) : (
              <div key={idx} className="space-y-2">
                {turn.toolCalls.length > 0 && (
                  <div className="space-y-1.5">
                    <SourceBadges calls={turn.toolCalls} />
                    {turn.toolCalls.map((call, ci) => (
                      <ToolCallCard key={ci} call={call} />
                    ))}
                  </div>
                )}
                <div
                  className="rounded px-3 py-2 text-sm bg-surface overflow-hidden"
                  style={{ border: '1px solid var(--border)' }}
                >
                  {turn.content ? (
                    <Markdown content={turn.content} />
                  ) : (
                    <span className="text-muted italic">(no answer)</span>
                  )}
                </div>
                {turn.model && (
                  <p className="text-[11px] text-muted">via {turn.model}</p>
                )}
              </div>
            )
          )}

          {loading && (
            <div className="text-sm text-muted flex items-center gap-2">
              <span className="inline-block w-2 h-2 rounded-full bg-accent animate-pulse" />
              Agent is searching…
            </div>
          )}

          {error && (
            <div
              style={{ border: '1px solid var(--danger)', color: 'var(--danger)' }}
              className="text-sm p-3 bg-[#fef2f2] rounded"
            >
              {error}
            </div>
          )}
        </div>
      </div>

      <div style={{ borderTop: '1px solid var(--border)' }} className="p-4 sm:px-8 bg-bg">
        <form onSubmit={onSubmit} className="page-content flex gap-2 items-end">
          <div className="flex-1">
            <Textarea
              className="min-h-[52px]"
              rows={2}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault()
                  void send(input)
                }
              }}
              placeholder="Ask the agent something… (Enter to send, Shift+Enter for newline)"
              disabled={loading}
            />
          </div>
          <Button type="submit" variant="primary" disabled={loading || !input.trim()} loading={loading}>
            Send
          </Button>
        </form>
      </div>
    </div>
  )
}
