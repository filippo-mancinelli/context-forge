import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import * as RadixSelect from '@radix-ui/react-select'
import {
  ChevronDown,
  ArrowDown,
  ArrowUp,
  Check,
  Copy,
  History,
  MessagesSquare,
  Share2,
  Square,
  SquarePen,
  Trash2,
  X,
} from 'lucide-react'
import {
  api,
  type ChatMessage,
  type ChatModel,
  type ChatSessionSummary,
  type ChatStreamEvent,
  type ChatToolCall,
  type StoredChatTurn,
} from '../lib/api'
import { Button, Dialog, DialogFooter, Spinner } from '../components/ui'
import {
  AssistantBody,
  PendingToolCard,
  ReasoningBlock,
  SourceBadges,
  ToolCallCard,
  CopyButton,
  UserBubble,
} from '../components/ChatTranscript'

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
const HISTORY_OPEN_KEY = 'cf_chat_history_open'

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

function timeAgo(iso?: string): string {
  if (!iso) return ''
  const s = (Date.now() - new Date(iso).getTime()) / 1000
  if (s < 60) return 'now'
  if (s < 3600) return `${Math.floor(s / 60)}m ago`
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`
  return `${Math.floor(s / 86400)}d ago`
}

/** Convert live turns to the persisted shape (drop transient stream state). */
function toStored(turns: Turn[]): StoredChatTurn[] {
  const out: StoredChatTurn[] = []
  for (const t of turns) {
    if (t.role === 'user') {
      out.push({ role: 'user', content: t.content })
    } else if (t.content || t.toolCalls.length > 0 || t.reasoning) {
      out.push({
        role: 'assistant',
        content: t.content,
        reasoning: t.reasoning || undefined,
        toolCalls: t.toolCalls.length > 0 ? t.toolCalls : undefined,
        model: t.model,
        stopped: t.stopped || undefined,
      })
    }
  }
  return out
}

function fromStored(turns: StoredChatTurn[]): Turn[] {
  return turns.map((t) =>
    t.role === 'user'
      ? ({ role: 'user', content: t.content } as UserTurn)
      : ({
          role: 'assistant',
          content: t.content ?? '',
          reasoning: t.reasoning ?? '',
          toolCalls: t.toolCalls ?? [],
          pendingTool: null,
          model: t.model,
          stopped: t.stopped,
          streaming: false,
        } as AssistantTurn)
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
        className="inline-flex items-center gap-1 px-2 py-1 text-xs text-muted hover:text-text hover:bg-surface rounded transition-colors focus:outline-none data-[disabled]:opacity-50 data-[disabled]:cursor-not-allowed min-w-0"
        aria-label="Model"
      >
        <span className="truncate">
          <RadixSelect.Value placeholder="Model" />
        </span>
        <ChevronDown className="w-3 h-3 flex-shrink-0" />
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

function SessionList({
  sessions,
  activeId,
  disabled,
  onSelect,
  onDelete,
}: {
  sessions: ChatSessionSummary[]
  activeId: number | null
  disabled: boolean
  onSelect: (id: number) => void
  onDelete: (id: number) => void
}) {
  if (sessions.length === 0) {
    return <p className="text-xs text-muted px-3 py-3">No saved chats yet.</p>
  }
  return (
    <div className="flex-1 overflow-y-auto scrollbar-thin py-1">
      {sessions.map((s) => {
        const active = s.id === activeId
        return (
          <div
            key={s.id}
            style={active ? { borderLeft: '2px solid var(--accent)' } : { borderLeft: '2px solid transparent' }}
            className={`group flex items-center gap-1 pr-1.5 ${
              active ? 'bg-[#eaf4fb]' : 'hover:bg-[color:var(--code-bg)]'
            }`}
          >
            <button
              onClick={() => onSelect(s.id)}
              disabled={disabled}
              className="flex-1 min-w-0 text-left px-2.5 py-2 disabled:cursor-not-allowed"
            >
              <span className="block text-xs text-text truncate">{s.title}</span>
              <span className="block text-[10px] text-muted">
                {timeAgo(s.updated_at)}
                {s.shared ? ' · shared' : ''}
              </span>
            </button>
            <button
              onClick={() => onDelete(s.id)}
              disabled={disabled}
              className="p-1.5 text-muted hover:text-[var(--danger)] opacity-60 md:opacity-0 md:group-hover:opacity-100 focus:opacity-100 transition-opacity flex-shrink-0"
              aria-label={`Delete "${s.title}"`}
              title="Delete chat"
            >
              <Trash2 className="w-3 h-3" />
            </button>
          </div>
        )
      })}
    </div>
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

  const [sessions, setSessions] = useState<ChatSessionSummary[]>([])
  const [sessionId, setSessionId] = useState<number | null>(null)
  const [historyOpen, setHistoryOpen] = useState(
    () => localStorage.getItem(HISTORY_OPEN_KEY) !== '0'
  )
  const [historyDrawer, setHistoryDrawer] = useState(false)

  const [shareOpen, setShareOpen] = useState(false)
  const [shareUrl, setShareUrl] = useState<string | null>(null)
  const [shareBusy, setShareBusy] = useState(false)
  const [shareError, setShareError] = useState<string | null>(null)
  const [shareCopied, setShareCopied] = useState(false)

  const scrollRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  const pinnedRef = useRef(true)
  const turnsRef = useRef<Turn[]>(turns)
  const sessionIdRef = useRef<number | null>(null)

  useEffect(() => {
    turnsRef.current = turns
  }, [turns])

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

    api.chat.sessions
      .list()
      .then((res) => setSessions(res.sessions))
      .catch(() => setSessions([]))
  }, [])

  useEffect(() => {
    if (selectedModel) localStorage.setItem(MODEL_STORAGE_KEY, selectedModel)
  }, [selectedModel])

  useEffect(() => {
    localStorage.setItem(HISTORY_OPEN_KEY, historyOpen ? '1' : '0')
  }, [historyOpen])

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

  /** Save the current conversation (create on first save). Best-effort. */
  const persist = async (): Promise<number | null> => {
    const stored = toStored(turnsRef.current)
    if (!stored.some((t) => t.role === 'assistant')) return sessionIdRef.current
    try {
      if (sessionIdRef.current == null) {
        const res = await api.chat.sessions.create(stored)
        sessionIdRef.current = res.session.id
        setSessionId(res.session.id)
        setSessions((prev) => [res.session, ...prev])
      } else {
        const res = await api.chat.sessions.update(sessionIdRef.current, stored)
        setSessions((prev) => [res.session, ...prev.filter((s) => s.id !== res.session.id)])
      }
    } catch {
      // History saving must never break the chat itself.
    }
    return sessionIdRef.current
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
      void persist()
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
    sessionIdRef.current = null
    setSessionId(null)
    textareaRef.current?.focus()
  }

  const openSession = (id: number) => {
    if (streaming || id === sessionIdRef.current) {
      setHistoryDrawer(false)
      return
    }
    api.chat.sessions
      .get(id)
      .then((res) => {
        sessionIdRef.current = id
        setSessionId(id)
        setTurns(fromStored(res.turns))
        setHistoryDrawer(false)
        pinnedRef.current = true
      })
      .catch(() => {})
  }

  const deleteSession = (id: number) => {
    if (streaming && id === sessionIdRef.current) return
    if (!window.confirm('Delete this chat? This cannot be undone.')) return
    api.chat.sessions
      .delete(id)
      .then(() => {
        setSessions((prev) => prev.filter((s) => s.id !== id))
        if (id === sessionIdRef.current) {
          sessionIdRef.current = null
          setSessionId(null)
          setTurns([])
        }
      })
      .catch(() => {})
  }

  const openShare = async () => {
    setShareOpen(true)
    setShareBusy(true)
    setShareError(null)
    setShareUrl(null)
    try {
      const id = await persist()
      if (id == null) {
        setShareError('Nothing to share yet — ask the agent something first.')
        return
      }
      const res = await api.chat.sessions.share(id)
      setShareUrl(`${window.location.origin}/share/chat/${res.share_token}`)
      setSessions((prev) => prev.map((s) => (s.id === id ? { ...s, shared: true } : s)))
    } catch (e) {
      setShareError(e instanceof Error ? e.message : String(e))
    } finally {
      setShareBusy(false)
    }
  }

  const revokeShare = async () => {
    const id = sessionIdRef.current
    if (id == null) return
    setShareBusy(true)
    try {
      await api.chat.sessions.unshare(id)
      setShareUrl(null)
      setShareOpen(false)
      setSessions((prev) => prev.map((s) => (s.id === id ? { ...s, shared: false } : s)))
    } catch (e) {
      setShareError(e instanceof Error ? e.message : String(e))
    } finally {
      setShareBusy(false)
    }
  }

  const copyShareUrl = () => {
    if (!shareUrl) return
    void navigator.clipboard.writeText(shareUrl).then(() => {
      setShareCopied(true)
      window.setTimeout(() => setShareCopied(false), 1500)
    })
  }

  const onSubmit = (e: FormEvent) => {
    e.preventDefault()
    void send(input)
  }

  const noKeys = modelsLoaded && models.length === 0
  const hasAnswer = turns.some((t) => t.role === 'assistant' && t.content)
  const lastTurn = turns[turns.length - 1]
  const canRetry = !streaming && lastTurn?.role === 'assistant' && !!lastTurn.error

  const headerButton =
    'p-1.5 rounded text-muted hover:text-text hover:bg-surface transition-colors disabled:opacity-40 disabled:cursor-not-allowed'

  return (
    <div className="flex h-full">
      {/* Main chat column */}
      <div className="flex flex-col flex-1 min-w-0">
        <div
          style={{ borderBottom: '1px solid var(--border)' }}
          className="px-4 sm:px-8 py-2.5 flex items-center justify-between gap-2 flex-shrink-0"
        >
          <h1 className="truncate" style={{ fontSize: '1rem', margin: 0 }}>
            Agent Chat
          </h1>
          <div className="flex items-center gap-1 flex-shrink-0">
            <button
              onClick={() => void openShare()}
              disabled={!hasAnswer || streaming}
              className={headerButton}
              title="Share this chat"
              aria-label="Share this chat"
            >
              <Share2 className="w-4 h-4" />
            </button>
            <button
              onClick={newChat}
              disabled={turns.length === 0}
              className={headerButton}
              title="New chat"
              aria-label="New chat"
            >
              <SquarePen className="w-4 h-4" />
            </button>
            {/* History: toggles the side panel on desktop, a drawer on mobile */}
            <button
              onClick={() => setHistoryOpen((o) => !o)}
              className={`${headerButton} hidden md:inline-flex ${historyOpen ? 'text-accent' : ''}`}
              title={historyOpen ? 'Hide history' : 'Show history'}
              aria-label={historyOpen ? 'Hide history' : 'Show history'}
            >
              <History className="w-4 h-4" />
            </button>
            <button
              onClick={() => setHistoryDrawer(true)}
              className={`${headerButton} md:hidden`}
              title="Chat history"
              aria-label="Chat history"
            >
              <History className="w-4 h-4" />
            </button>
          </div>
        </div>

        <div className="relative flex-1 min-h-0">
          <div ref={scrollRef} onScroll={onScroll} className="h-full overflow-y-auto px-4 sm:px-8">
            {turns.length === 0 ? (
              <div className="min-h-full flex items-center justify-center">
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
                    <UserBubble key={idx} content={turn.content} />
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
                        <AssistantBody content={turn.content} streaming={turn.streaming} />
                      ) : (
                        turn.streaming &&
                        !turn.pendingTool &&
                        !turn.reasoning && (
                          <div className="flex items-center gap-2 text-sm text-muted px-1 py-1.5">
                            <Spinner size={13} />
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

          {showJump && turns.length > 0 && (
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
                  className="w-full resize-none bg-transparent px-3 pt-2.5 pb-1 text-base sm:text-sm focus:outline-none placeholder:text-muted min-h-[40px] max-h-[200px]"
                />
                <div className="flex items-center justify-between gap-2 px-2 pb-2">
                  <ModelSelect
                    models={models}
                    value={selectedModel}
                    onChange={setSelectedModel}
                    disabled={streaming}
                  />
                  <div className="flex items-center gap-3 flex-shrink-0">
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

      {/* History panel — slim, collapsible, desktop only */}
      {historyOpen && (
        <aside
          style={{ borderLeft: '1px solid var(--border)', width: '208px' }}
          className="hidden md:flex flex-col flex-shrink-0 bg-surface"
        >
          <div
            style={{ borderBottom: '1px solid var(--border)' }}
            className="px-3 py-2.5 flex items-center justify-between"
          >
            <span className="text-xs font-medium text-muted uppercase tracking-wider">
              History
            </span>
            <button
              onClick={() => setHistoryOpen(false)}
              className="text-muted hover:text-text transition-colors p-0.5"
              aria-label="Hide history"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
          <SessionList
            sessions={sessions}
            activeId={sessionId}
            disabled={streaming}
            onSelect={openSession}
            onDelete={deleteSession}
          />
        </aside>
      )}

      {/* History drawer — mobile */}
      {historyDrawer && (
        <>
          <div
            className="fixed inset-0 bg-black/40 z-40 md:hidden"
            onClick={() => setHistoryDrawer(false)}
            aria-hidden="true"
          />
          <div
            style={{ borderLeft: '1px solid var(--border)', width: '260px' }}
            className="fixed top-0 right-0 h-full bg-surface z-50 flex flex-col md:hidden"
          >
            <div
              style={{ borderBottom: '1px solid var(--border)' }}
              className="px-3 py-3 flex items-center justify-between"
            >
              <span className="text-xs font-medium text-muted uppercase tracking-wider">
                History
              </span>
              <button
                onClick={() => setHistoryDrawer(false)}
                className="text-muted hover:text-text transition-colors p-1"
                aria-label="Close history"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
            <SessionList
              sessions={sessions}
              activeId={sessionId}
              disabled={streaming}
              onSelect={openSession}
              onDelete={deleteSession}
            />
          </div>
        </>
      )}

      {/* Share dialog */}
      <Dialog
        open={shareOpen}
        onOpenChange={(o) => {
          setShareOpen(o)
          if (!o) setShareError(null)
        }}
        title="Share this chat"
        description="Anyone with the link can view the conversation up to this point — later messages stay private until you share again."
      >
        {shareBusy && !shareUrl ? (
          <div className="flex items-center gap-2 text-sm text-muted py-2">
            <Spinner size={13} />
            Creating link…
          </div>
        ) : shareError ? (
          <p className="text-sm" style={{ color: 'var(--danger)' }}>
            {shareError}
          </p>
        ) : shareUrl ? (
          <div className="flex items-center gap-2">
            <input
              readOnly
              value={shareUrl}
              onFocus={(e) => e.target.select()}
              className="flex-1 min-w-0 px-2.5 py-1.5 text-xs font-mono bg-surface border border-border rounded focus:outline-none focus:border-accent"
              aria-label="Public link"
            />
            <Button variant="secondary" size="sm" onClick={copyShareUrl}>
              {shareCopied ? <Check className="w-3.5 h-3.5 text-success" /> : <Copy className="w-3.5 h-3.5" />}
              {shareCopied ? 'Copied' : 'Copy'}
            </Button>
          </div>
        ) : null}
        <DialogFooter>
          {shareUrl && (
            <Button variant="danger" size="sm" onClick={() => void revokeShare()} disabled={shareBusy}>
              Revoke link
            </Button>
          )}
          <Button variant="secondary" size="sm" onClick={() => setShareOpen(false)}>
            Done
          </Button>
        </DialogFooter>
      </Dialog>
    </div>
  )
}
