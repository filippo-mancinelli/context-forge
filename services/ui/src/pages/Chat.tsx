import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import * as RadixSelect from '@radix-ui/react-select'
import {
  GitBranch,
  Brain,
  Library,
  Database,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  AlertCircle,
  ArrowDown,
  ArrowUp,
  Check,
  Copy,
  Loader2,
  MessagesSquare,
  Sparkles,
  Square,
} from 'lucide-react'
import {
  api,
  type ChatMessage,
  type ChatModel,
  type ChatStreamEvent,
  type ChatToolCall,
} from '../lib/api'
import { Button } from '../components/ui'
import { Markdown } from '../components/Markdown'

interface PendingTool {
  tool: string
  source: ChatToolCall['source']
  query: string
}

interface AssistantTurn {
  role: 'assistant'
  content: string
  reasoning: string
  toolCalls: ChatToolCall[]
  pendingTool: PendingTool | null
  model?: string
  streaming: boolean
  stopped?: boolean
  error?: string
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
  databases: { label: 'Databases', icon: Database, color: '#d97706' },
}

const PROVIDER_LABELS: Record<string, string> = {
  openai: 'OpenAI',
  anthropic: 'Anthropic',
  deepseek: 'DeepSeek',
}

const SUGGESTIONS = [
  'What repositories are indexed and what do they do?',
  'What do you remember about my preferences or past decisions?',
  'Summarize what the uploaded documents cover.',
  'What tables exist in the connected databases?',
]

const MODEL_STORAGE_KEY = 'cf_chat_model'

function modelKey(m: { provider: string; id: string }): string {
  return `${m.provider}::${m.id}`
}

function emptyAssistant(): AssistantTurn {
  return {
    role: 'assistant',
    content: '',
    reasoning: '',
    toolCalls: [],
    pendingTool: null,
    streaming: true,
  }
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

function PendingToolCard({ pending }: { pending: PendingTool }) {
  const meta = SOURCE_META[pending.source]
  const Icon = meta.icon
  return (
    <div
      style={{ border: '1px solid var(--border)' }}
      className="rounded bg-surface flex items-center gap-2 px-3 py-2"
    >
      <Loader2 className="w-3.5 h-3.5 animate-spin text-muted flex-shrink-0" />
      <Icon className="w-3.5 h-3.5 flex-shrink-0" style={{ color: meta.color }} />
      <span className="text-xs font-medium" style={{ color: meta.color }}>
        {meta.label}
      </span>
      <span className="text-xs text-muted truncate flex-1">“{pending.query}”</span>
      <span className="text-xs text-muted flex-shrink-0">searching…</span>
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

function ReasoningBlock({
  text,
  streaming,
  hasAnswer,
}: {
  text: string
  streaming: boolean
  hasAnswer: boolean
}) {
  const [userOpen, setUserOpen] = useState<boolean | null>(null)
  // Auto-expand while the model is only reasoning; collapse once the answer starts.
  const open = userOpen ?? (streaming && !hasAnswer)
  const active = streaming && !hasAnswer
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
          {text}
        </div>
      )}
    </div>
  )
}

function CopyButton({ text }: { text: string }) {
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

function ModelSelect({
  models,
  value,
  onChange,
  disabled,
}: {
  models: ChatModel[]
  value: string
  onChange: (v: string) => void
  disabled?: boolean
}) {
  const grouped = useMemo(() => {
    const byProvider = new Map<string, ChatModel[]>()
    for (const m of models) {
      const list = byProvider.get(m.provider) ?? []
      list.push(m)
      byProvider.set(m.provider, list)
    }
    return [...byProvider.entries()]
  }, [models])

  if (models.length === 0) return null

  return (
    <RadixSelect.Root value={value} onValueChange={onChange} disabled={disabled}>
      <RadixSelect.Trigger
        className="inline-flex items-center gap-1 px-2 py-1 text-xs text-muted hover:text-text hover:bg-surface rounded transition-colors focus:outline-none data-[disabled]:opacity-50 data-[disabled]:cursor-not-allowed"
        aria-label="Model"
      >
        <RadixSelect.Value placeholder="Model" />
        <ChevronDown className="w-3 h-3" />
      </RadixSelect.Trigger>
      <RadixSelect.Portal>
        <RadixSelect.Content
          position="popper"
          side="top"
          sideOffset={4}
          className="z-50 bg-bg border border-border shadow-md overflow-hidden rounded"
        >
          <RadixSelect.Viewport className="p-1 max-h-72">
            {grouped.map(([provider, list]) => (
              <RadixSelect.Group key={provider}>
                <RadixSelect.Label className="px-2 pt-1.5 pb-0.5 text-[10px] uppercase tracking-wider text-muted">
                  {PROVIDER_LABELS[provider] ?? provider}
                </RadixSelect.Label>
                {list.map((m) => (
                  <RadixSelect.Item
                    key={modelKey(m)}
                    value={modelKey(m)}
                    className="flex items-center justify-between gap-3 px-2 py-1 text-xs text-text cursor-pointer rounded select-none hover:bg-surface focus:bg-surface focus:outline-none"
                  >
                    <RadixSelect.ItemText>{m.label}</RadixSelect.ItemText>
                    <RadixSelect.ItemIndicator>
                      <Check className="w-3 h-3 text-accent" />
                    </RadixSelect.ItemIndicator>
                  </RadixSelect.Item>
                ))}
              </RadixSelect.Group>
            ))}
          </RadixSelect.Viewport>
        </RadixSelect.Content>
      </RadixSelect.Portal>
    </RadixSelect.Root>
  )
}

function applyEvent(prev: Turn[], ev: ChatStreamEvent): Turn[] {
  const last = prev[prev.length - 1]
  if (!last || last.role !== 'assistant') return prev
  const t: AssistantTurn = { ...last }
  switch (ev.type) {
    case 'reasoning':
      t.reasoning += ev.delta
      break
    case 'text':
      t.content += ev.delta
      break
    case 'tool_start':
      t.pendingTool = { tool: ev.tool, source: ev.source, query: ev.query }
      break
    case 'tool_result': {
      const { type: _type, ...call } = ev
      t.toolCalls = [...t.toolCalls, call]
      t.pendingTool = null
      break
    }
    case 'done':
      t.model = ev.model
      t.streaming = false
      t.pendingTool = null
      break
    case 'error':
      t.error = ev.message
      t.streaming = false
      t.pendingTool = null
      break
  }
  return [...prev.slice(0, -1), t]
}

function patchLastAssistant(prev: Turn[], patch: (t: AssistantTurn) => AssistantTurn): Turn[] {
  const last = prev[prev.length - 1]
  if (!last || last.role !== 'assistant') return prev
  return [...prev.slice(0, -1), patch(last)]
}

export default function Chat() {
  const [turns, setTurns] = useState<Turn[]>([])
  const [input, setInput] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [models, setModels] = useState<ChatModel[]>([])
  const [modelsLoaded, setModelsLoaded] = useState(false)
  const [selectedModel, setSelectedModel] = useState<string>(
    () => localStorage.getItem(MODEL_STORAGE_KEY) ?? ''
  )
  const [showJump, setShowJump] = useState(false)

  const scrollRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  const pinnedRef = useRef(true)

  useEffect(() => {
    api.chat
      .models()
      .then((res) => {
        setModels(res.models)
        setSelectedModel((cur) => {
          if (res.models.some((m) => modelKey(m) === cur)) return cur
          const def =
            (res.default &&
              res.models.find(
                (m) => m.provider === res.default!.provider && m.id === res.default!.model
              )) ||
            res.models[0]
          return def ? modelKey(def) : ''
        })
      })
      .catch(() => setModels([]))
      .finally(() => setModelsLoaded(true))
  }, [])

  useEffect(() => {
    if (selectedModel) localStorage.setItem(MODEL_STORAGE_KEY, selectedModel)
  }, [selectedModel])

  // Stick to the bottom while streaming unless the user scrolled up.
  useEffect(() => {
    if (pinnedRef.current) {
      scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight })
    }
  }, [turns])

  // Abort any in-flight stream when leaving the page.
  useEffect(() => () => abortRef.current?.abort(), [])

  const onScroll = () => {
    const el = scrollRef.current
    if (!el) return
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80
    pinnedRef.current = atBottom
    setShowJump(!atBottom)
  }

  const jumpToBottom = () => {
    pinnedRef.current = true
    setShowJump(false)
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }

  const resizeTextarea = () => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`
  }

  const send = async (text: string, base: Turn[] = turns) => {
    const trimmed = text.trim()
    if (!trimmed || streaming) return

    const history: ChatMessage[] = [
      ...base
        .filter((t) => t.role === 'user' || t.content)
        .map((t) => ({ role: t.role, content: t.content })),
      { role: 'user', content: trimmed },
    ]
    setTurns([...base, { role: 'user', content: trimmed }, emptyAssistant()])
    setInput('')
    requestAnimationFrame(resizeTextarea)
    pinnedRef.current = true
    setStreaming(true)

    const controller = new AbortController()
    abortRef.current = controller
    const [provider, model] = selectedModel ? selectedModel.split('::') : []
    try {
      await api.chat.stream(
        { messages: history, provider, model },
        (ev) => setTurns((prev) => applyEvent(prev, ev)),
        controller.signal
      )
    } catch (e) {
      const aborted = controller.signal.aborted
      setTurns((prev) =>
        patchLastAssistant(prev, (t) => ({
          ...t,
          stopped: aborted || t.stopped,
          error: aborted ? t.error : e instanceof Error ? e.message : String(e),
        }))
      )
    } finally {
      abortRef.current = null
      setStreaming(false)
      setTurns((prev) =>
        patchLastAssistant(prev, (t) => ({ ...t, streaming: false, pendingTool: null }))
      )
    }
  }

  const stop = () => abortRef.current?.abort()

  const retry = () => {
    // Drop the failed assistant turn + its user turn, then resend.
    const lastUser = [...turns].reverse().find((t) => t.role === 'user')
    if (!lastUser || streaming) return
    void send(lastUser.content, turns.slice(0, -2))
  }

  const newChat = () => {
    abortRef.current?.abort()
    setTurns([])
    textareaRef.current?.focus()
  }

  const onSubmit = (e: FormEvent) => {
    e.preventDefault()
    void send(input)
  }

  const noKeys = modelsLoaded && models.length === 0
  const lastTurn = turns[turns.length - 1]
  const canRetry =
    !streaming && lastTurn?.role === 'assistant' && !!lastTurn.error

  return (
    <div className="flex flex-col h-full">
      <div
        style={{ borderBottom: '1px solid var(--border)' }}
        className="px-4 sm:px-8 py-3 flex items-center justify-between gap-4 flex-shrink-0"
      >
        <h1 style={{ fontSize: '1rem', margin: 0 }}>Agent Chat</h1>
        <Button variant="ghost" size="sm" onClick={newChat} disabled={turns.length === 0}>
          New chat
        </Button>
      </div>

      <div className="relative flex-1 min-h-0">
        <div ref={scrollRef} onScroll={onScroll} className="h-full overflow-y-auto px-4 sm:px-8">
          {turns.length === 0 ? (
            <div className="h-full flex items-center justify-center">
              <div className="max-w-md text-center space-y-4 py-8">
                <MessagesSquare className="w-8 h-8 text-muted mx-auto" strokeWidth={1.5} />
                <div>
                  <p className="text-sm font-medium mb-1">Ask your context</p>
                  <p className="text-sm text-muted">
                    Chat with a retrieval agent that searches your repositories, memory,
                    knowledge base and databases. Every search it runs is shown inline.
                  </p>
                </div>
                <div className="flex flex-wrap justify-center gap-2">
                  {SUGGESTIONS.map((s) => (
                    <button
                      key={s}
                      onClick={() => void send(s)}
                      disabled={streaming || noKeys}
                      style={{ border: '1px solid var(--border)' }}
                      className="text-xs text-muted hover:text-accent hover:border-[var(--accent)] rounded-full px-3 py-1.5 transition-colors disabled:opacity-50"
                    >
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          ) : (
            <div className="page-content space-y-5 py-4">
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
                    {turn.pendingTool && <PendingToolCard pending={turn.pendingTool} />}
                    {turn.reasoning && (
                      <ReasoningBlock
                        text={turn.reasoning}
                        streaming={turn.streaming}
                        hasAnswer={turn.content.length > 0}
                      />
                    )}
                    {turn.content ? (
                      <div
                        className="rounded px-3 py-2 text-sm bg-surface overflow-hidden"
                        style={{ border: '1px solid var(--border)' }}
                      >
                        <Markdown content={turn.content} />
                        {turn.streaming && (
                          <span className="inline-block w-1.5 h-3.5 bg-accent animate-pulse rounded-sm mt-1" />
                        )}
                      </div>
                    ) : (
                      turn.streaming &&
                      !turn.pendingTool &&
                      !turn.reasoning && (
                        <div className="flex items-center gap-2 text-sm text-muted px-1 py-1.5">
                          <Loader2 className="w-3.5 h-3.5 animate-spin" />
                          Thinking…
                        </div>
                      )
                    )}
                    {turn.error && (
                      <div
                        style={{ border: '1px solid var(--danger)', color: 'var(--danger)' }}
                        className="text-sm p-3 bg-[#fef2f2] rounded flex items-start justify-between gap-3"
                      >
                        <span className="break-words min-w-0">{turn.error}</span>
                        {canRetry && idx === turns.length - 1 && (
                          <button onClick={retry} className="underline flex-shrink-0 text-sm">
                            Retry
                          </button>
                        )}
                      </div>
                    )}
                    {!turn.streaming && (turn.content || turn.model) && (
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
          )}
        </div>

        {showJump && (
          <button
            onClick={jumpToBottom}
            style={{ border: '1px solid var(--border)' }}
            className="absolute bottom-4 left-1/2 -translate-x-1/2 bg-bg shadow-md rounded-full p-2 text-muted hover:text-text transition-colors"
            aria-label="Scroll to bottom"
          >
            <ArrowDown className="w-4 h-4" />
          </button>
        )}
      </div>

      <div className="px-4 sm:px-8 pb-4 pt-2 flex-shrink-0">
        <form onSubmit={onSubmit} className="page-content">
          {noKeys ? (
            <div
              style={{ border: '1px solid var(--border)' }}
              className="rounded bg-surface px-3 py-3 text-sm text-muted"
            >
              No LLM API key configured. Add an OpenAI, Anthropic or DeepSeek key in{' '}
              <Link to="/settings" className="text-accent underline">
                Settings → LLM
              </Link>{' '}
              to use the agent chat.
            </div>
          ) : (
            <div
              style={{ border: '1px solid var(--border)' }}
              className="rounded bg-bg focus-within:border-[var(--accent)] transition-colors"
            >
              <textarea
                ref={textareaRef}
                rows={1}
                value={input}
                onChange={(e) => {
                  setInput(e.target.value)
                  resizeTextarea()
                }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault()
                    void send(input)
                  }
                }}
                placeholder="Ask the agent something…"
                className="w-full resize-none bg-transparent px-3 pt-2.5 pb-1 text-sm focus:outline-none placeholder:text-muted min-h-[40px] max-h-[200px]"
              />
              <div className="flex items-center justify-between gap-2 px-2 pb-2">
                <ModelSelect
                  models={models}
                  value={selectedModel}
                  onChange={setSelectedModel}
                  disabled={streaming}
                />
                <div className="flex items-center gap-3">
                  <span className="hidden sm:block text-[11px] text-muted">
                    Enter to send · Shift+Enter for newline
                  </span>
                  {streaming ? (
                    <button
                      type="button"
                      onClick={stop}
                      style={{ border: '1px solid var(--border)' }}
                      className="rounded-full p-2 text-text hover:bg-surface transition-colors"
                      title="Stop generating"
                      aria-label="Stop generating"
                    >
                      <Square className="w-3.5 h-3.5 fill-current" />
                    </button>
                  ) : (
                    <button
                      type="submit"
                      disabled={!input.trim()}
                      className="rounded-full p-2 bg-accent text-white hover:bg-accent-hover transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                      title="Send"
                      aria-label="Send"
                    >
                      <ArrowUp className="w-3.5 h-3.5" />
                    </button>
                  )}
                </div>
              </div>
            </div>
          )}
        </form>
      </div>
    </div>
  )
}
