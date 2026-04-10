import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { Brain, CheckCircle, Loader2, Plus, Search, Trash2, X } from 'lucide-react'
import { api, type Memory as MemoryItem } from '../lib/api'

function parseMetadataInput(value: string): Record<string, unknown> | undefined {
  const trimmed = value.trim()
  if (!trimmed) return undefined

  let parsed: unknown
  try {
    parsed = JSON.parse(trimmed)
  } catch {
    throw new Error('Metadata must be valid JSON.')
  }

  if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') {
    throw new Error('Metadata JSON must be an object.')
  }

  return parsed as Record<string, unknown>
}

function formatDate(iso?: string) {
  if (!iso) return null
  return new Date(iso).toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function MemoryCard({ memory, onDelete }: { memory: MemoryItem; onDelete: (id: string) => Promise<void> }) {
  const [deleting, setDeleting] = useState(false)

  const handleDelete = async () => {
    setDeleting(true)
    try {
      await onDelete(memory.id)
    } finally {
      setDeleting(false)
    }
  }

  return (
    <div className="group rounded-xl border border-gray-800 bg-gray-900/80 p-3 transition-colors hover:border-gray-700">
      <div className="flex items-start justify-between gap-3">
        <p className="flex-1 text-sm leading-relaxed text-gray-200">{memory.memory}</p>
        <button
          onClick={handleDelete}
          disabled={deleting}
          className="flex-shrink-0 rounded-lg p-1 text-gray-600 transition-all hover:bg-gray-800 hover:text-rose-400 disabled:opacity-50 md:opacity-0 md:group-hover:opacity-100"
          title="Delete memory"
        >
          {deleting ? <Loader2 className="h-3 w-3 animate-spin" /> : <Trash2 className="h-3 w-3" />}
        </button>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        {memory.score !== undefined && (
          <span className="rounded bg-indigo-500/10 px-1.5 py-0.5 font-mono text-[10px] text-indigo-300">
            {memory.score.toFixed(3)}
          </span>
        )}
        {memory.metadata && Object.keys(memory.metadata).length > 0 && (
          <div className="flex flex-wrap gap-1">
            {Object.entries(memory.metadata).slice(0, 4).map(([key, value]) => (
              <span key={key} className="rounded bg-gray-800 px-1.5 py-0.5 font-mono text-[10px] text-gray-400">
                {key}: {String(value)}
              </span>
            ))}
          </div>
        )}
        {memory.created_at && (
          <span className="ml-auto text-[10px] text-gray-500">
            {formatDate(memory.created_at)}
          </span>
        )}
      </div>
    </div>
  )
}

export default function Memory() {
  const [memories, setMemories] = useState<MemoryItem[]>([])
  const [loading, setLoading] = useState(true)
  const [query, setQuery] = useState('')
  const [searching, setSearching] = useState(false)
  const [isSearchMode, setIsSearchMode] = useState(false)
  const [pageError, setPageError] = useState<string | null>(null)
  const [content, setContent] = useState('')
  const [metadataInput, setMetadataInput] = useState('')
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)
  const [createSuccess, setCreateSuccess] = useState<string | null>(null)

  const loadAll = useCallback(async () => {
    try {
      const data = await api.memory.list(100)
      setMemories(data.memories)
      setPageError(null)
    } catch (e) {
      setPageError(String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadAll()
  }, [loadAll])

  const handleSearch = async () => {
    if (!query.trim()) {
      setIsSearchMode(false)
      await loadAll()
      return
    }

    setSearching(true)
    setIsSearchMode(true)
    try {
      const data = await api.memory.search(query.trim(), 30)
      setMemories(data.memories)
      setPageError(null)
    } catch (e) {
      setPageError(String(e))
    } finally {
      setSearching(false)
    }
  }

  const handleClear = async () => {
    setQuery('')
    setIsSearchMode(false)
    await loadAll()
  }

  const handleDelete = async (id: string) => {
    try {
      await api.memory.delete(id)
      setMemories((prev) => prev.filter((memory) => memory.id !== id))
      setPageError(null)
    } catch (e) {
      setPageError(String(e))
    }
  }

  const handleCreate = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()

    const trimmedContent = content.trim()
    if (!trimmedContent) {
      setCreateError('Write the memory content before saving.')
      setCreateSuccess(null)
      return
    }

    let metadata: Record<string, unknown> | undefined
    try {
      metadata = parseMetadataInput(metadataInput)
    } catch (e) {
      setCreateError(e instanceof Error ? e.message : String(e))
      setCreateSuccess(null)
      return
    }

    setCreating(true)
    setCreateError(null)
    setCreateSuccess(null)

    try {
      await api.memory.create({
        content: trimmedContent,
        metadata,
        infer: false,
      })
      setContent('')
      setMetadataInput('')
      setQuery('')
      setIsSearchMode(false)
      setCreateSuccess('Memory saved successfully.')
      await loadAll()
    } catch (e) {
      setCreateError(String(e))
    } finally {
      setCreating(false)
    }
  }

  return (
    <div className="min-h-screen bg-gray-950">
      <div className="mx-auto max-w-6xl p-6">
        <div className="mb-6 flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
          <div>
            <p className="text-xs font-medium uppercase tracking-[0.18em] text-indigo-400">Memory</p>
            <h1 className="mt-1 flex items-center gap-2 text-xl font-semibold text-white">
              <Brain className="h-5 w-5 text-indigo-400" />
              Persistent notes
            </h1>
          </div>

          <div className="grid gap-2 sm:grid-cols-2 xl:w-[20rem]">
            <div className="rounded-xl border border-gray-800 bg-gray-900/70 p-3">
              <p className="text-[10px] uppercase tracking-wider text-gray-500">Visible</p>
              <p className="mt-1 text-xl font-semibold text-white">{memories.length}</p>
              <p className="mt-0.5 text-[10px] text-gray-500">{isSearchMode ? 'results' : 'memories'}</p>
            </div>
            <div className="rounded-xl border border-gray-800 bg-gray-900/70 p-3">
              <p className="text-[10px] uppercase tracking-wider text-gray-500">Mode</p>
              <p className="mt-1 text-xl font-semibold text-white">{isSearchMode ? 'Search' : 'Browse'}</p>
              <p className="mt-0.5 text-[10px] text-gray-500 truncate">
                {isSearchMode ? `"${query}"` : 'manual entry'}
              </p>
            </div>
          </div>
        </div>

        <div className="mb-5 grid gap-3 xl:grid-cols-[1.15fr_0.85fr]">
          <section className="rounded-xl border border-gray-800 bg-gradient-to-br from-gray-900 via-gray-900 to-gray-950 p-4">
            <p className="text-xs font-medium uppercase tracking-[0.18em] text-indigo-400">Manual Entry</p>
            <h2 className="mt-1 text-base font-semibold text-white">Add memory</h2>

            <form className="mt-4 space-y-3" onSubmit={handleCreate}>
              <label className="block">
                <span className="mb-1 block text-xs font-medium uppercase tracking-wider text-gray-500">Content</span>
                <textarea
                  value={content}
                  onChange={(event) => setContent(event.target.value)}
                  rows={4}
                  placeholder="The billing service now reads provider tokens from runtime settings."
                  className="w-full rounded-lg border border-gray-700 bg-gray-950 px-2.5 py-2 text-sm text-gray-100 placeholder-gray-500 outline-none transition-colors focus:border-indigo-500"
                />
              </label>

              <label className="block">
                <span className="mb-1 block text-xs font-medium uppercase tracking-wider text-gray-500">Metadata JSON (optional)</span>
                <textarea
                  value={metadataInput}
                  onChange={(event) => setMetadataInput(event.target.value)}
                  rows={3}
                  placeholder={'{"source":"dashboard","type":"decision"}'}
                  className="w-full rounded-lg border border-gray-700 bg-gray-950 px-2.5 py-2 font-mono text-xs text-gray-200 placeholder-gray-600 outline-none transition-colors focus:border-indigo-500"
                />
              </label>

              <div className="flex justify-end">
                <button
                  type="submit"
                  disabled={creating || !content.trim()}
                  className="inline-flex items-center justify-center gap-2 rounded-lg bg-indigo-600 px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-indigo-500 disabled:opacity-50"
                >
                  {creating ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Plus className="h-3.5 w-3.5" />}
                  Add memory
                </button>
              </div>
            </form>

            {createError && (
              <div className="mt-3 rounded-lg border border-rose-500/20 bg-rose-500/10 p-2.5 text-sm text-rose-300">
                {createError}
              </div>
            )}

            {createSuccess && (
              <div className="mt-3 flex items-center gap-2 rounded-lg border border-emerald-500/20 bg-emerald-500/10 p-2.5 text-sm text-emerald-300">
                <CheckCircle className="h-3.5 w-3.5 flex-shrink-0" />
                {createSuccess}
              </div>
            )}
          </section>

          <section className="rounded-xl border border-gray-800 bg-gray-900/70 p-4">
            <p className="text-xs font-medium uppercase tracking-[0.18em] text-gray-500">Search</p>
            <h2 className="mt-1 text-base font-semibold text-white">Find memories</h2>

            <div className="mt-4 flex gap-2">
              <div className="relative flex-1">
                <Search className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-500" />
                <input
                  type="text"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  onKeyDown={(event) => event.key === 'Enter' && handleSearch()}
                  placeholder="Search memories..."
                  className="w-full rounded-lg border border-gray-700 bg-gray-950 py-2 pl-9 pr-2.5 text-sm text-gray-200 placeholder-gray-600 outline-none transition-colors focus:border-indigo-500"
                />
              </div>
              {isSearchMode && (
                <button
                  onClick={() => {
                    void handleClear()
                  }}
                  className="rounded-lg border border-gray-700 bg-gray-950 px-2.5 py-2 text-gray-400 transition-colors hover:bg-gray-800"
                  title="Clear search"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              )}
              <button
                onClick={() => {
                  void handleSearch()
                }}
                disabled={searching}
                className="inline-flex items-center justify-center gap-2 rounded-lg bg-gray-100 px-3 py-2 text-sm font-medium text-gray-950 transition-colors hover:bg-white disabled:opacity-50"
              >
                {searching ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : 'Search'}
              </button>
            </div>

            <div className="mt-3 rounded-lg border border-gray-800 bg-gray-950/60 p-2.5 text-sm text-gray-400">
              {isSearchMode
                ? `${memories.length} result${memories.length === 1 ? '' : 's'} for "${query}"`
                : `Browsing ${memories.length} memories`}
            </div>
          </section>
        </div>

        {pageError && (
          <div className="mb-5 rounded-xl border border-rose-500/20 bg-rose-500/10 p-3 text-sm text-rose-300">
            <span className="font-medium">API error:</span> {pageError}
          </div>
        )}

        {loading ? (
          <div className="flex h-40 items-center justify-center text-gray-600">
            <Loader2 className="mr-2 h-5 w-5 animate-spin" />
            Loading...
          </div>
        ) : memories.length === 0 ? (
          <div className="rounded-xl border border-dashed border-gray-800 bg-gray-900/40 py-16 text-center text-gray-500">
            <Brain className="mx-auto mb-3 h-10 w-10 opacity-30" />
            <p className="text-sm">{isSearchMode ? 'No memories match your query.' : 'No memories stored yet.'}</p>
          </div>
        ) : (
          <div className="space-y-2">
            {memories.map((memory) => (
              <MemoryCard key={memory.id} memory={memory} onDelete={handleDelete} />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
