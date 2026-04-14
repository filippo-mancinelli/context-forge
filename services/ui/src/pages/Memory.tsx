import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { Trash2 } from 'lucide-react'
import { api, type Memory as MemoryItem } from '../lib/api'
import { Button, Input, Textarea } from '../components/ui'

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

function MemoryRow({ memory, onDelete }: { memory: MemoryItem; onDelete: (id: string) => Promise<void> }) {
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
    <tr style={{ borderBottom: '1px solid var(--border)' }} className="last:border-b-0 group">
      <td className="py-3 px-4 align-top">
        <p className="text-sm">{memory.memory}</p>
        {memory.metadata && Object.keys(memory.metadata).length > 0 && (
          <div className="flex flex-wrap gap-1 mt-1.5">
            {Object.entries(memory.metadata).slice(0, 4).map(([key, value]) => (
              <code key={key} className="text-xs font-mono text-muted bg-surface border border-border px-1.5 py-0.5">
                {key}: {String(value)}
              </code>
            ))}
          </div>
        )}
      </td>
      <td className="py-3 px-4 align-top w-28 text-xs text-muted whitespace-nowrap">
        {memory.score !== undefined && (
          <span className="font-mono text-accent block">{memory.score.toFixed(3)}</span>
        )}
        {formatDate(memory.created_at)}
      </td>
      <td className="py-3 px-4 align-top w-10">
        <button
          onClick={handleDelete}
          disabled={deleting}
          className="text-muted hover:text-danger transition-colors opacity-100 md:opacity-0 md:group-hover:opacity-100 disabled:opacity-50"
          title="Delete"
        >
          <Trash2 className="w-3.5 h-3.5" />
        </button>
      </td>
    </tr>
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
  const [createSuccess, setCreateSuccess] = useState(false)

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

  useEffect(() => { loadAll() }, [loadAll])

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
      setMemories(prev => prev.filter(m => m.id !== id))
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
      return
    }
    let metadata: Record<string, unknown> | undefined
    try {
      metadata = parseMetadataInput(metadataInput)
    } catch (e) {
      setCreateError(e instanceof Error ? e.message : String(e))
      return
    }
    setCreating(true)
    setCreateError(null)
    setCreateSuccess(false)
    try {
      await api.memory.create({ content: trimmedContent, metadata, infer: false })
      setContent('')
      setMetadataInput('')
      setQuery('')
      setIsSearchMode(false)
      setCreateSuccess(true)
      await loadAll()
    } catch (e) {
      setCreateError(String(e))
    } finally {
      setCreating(false)
    }
  }

  return (
    <div className="p-4 sm:p-8">
      <div className="page-content">
        <div className="mb-6">
          <h1>Memory</h1>
          <p className="text-muted text-sm">
            {isSearchMode
              ? `${memories.length} result${memories.length === 1 ? '' : 's'} for "${query}"`
              : `${memories.length} stored memories`}
          </p>
        </div>

        {pageError && (
          <div
            style={{ border: '1px solid var(--danger)', color: 'var(--danger)' }}
            className="text-sm p-3 mb-4 bg-[#fef2f2]"
          >
            {pageError}
          </div>
        )}

        {/* Add memory form */}
        <section style={{ border: '1px solid var(--border)' }} className="mb-6 p-4">
          <h2 className="text-base font-semibold mb-4">Add memory</h2>
          <form onSubmit={handleCreate} className="space-y-3">
            <Textarea
              label="Content"
              value={content}
              onChange={e => setContent(e.target.value)}
              rows={4}
              placeholder="The billing service now reads provider tokens from runtime settings."
            />
            <Textarea
              label="Metadata JSON (optional)"
              value={metadataInput}
              onChange={e => setMetadataInput(e.target.value)}
              rows={2}
              placeholder='{"source":"dashboard","type":"decision"}'
              className="font-mono text-xs"
            />
            {createError && (
              <p style={{ color: 'var(--danger)' }} className="text-sm">{createError}</p>
            )}
            {createSuccess && (
              <p style={{ color: 'var(--success)' }} className="text-sm">Memory saved.</p>
            )}
            <div className="flex justify-end">
              <Button
                type="submit"
                variant="primary"
                loading={creating}
                disabled={creating || !content.trim()}
              >
                Add memory
              </Button>
            </div>
          </form>
        </section>

        {/* Search */}
        <div className="flex gap-2 mb-4">
          <Input
            className="flex-1"
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && void handleSearch()}
            placeholder="Search memories..."
          />
          {isSearchMode && (
            <Button variant="ghost" onClick={() => void handleClear()}>
              Clear
            </Button>
          )}
          <Button
            variant="secondary"
            onClick={() => void handleSearch()}
            loading={searching}
            disabled={searching}
          >
            Search
          </Button>
        </div>

        {/* Memory list */}
        {loading ? (
          <p className="text-muted text-sm">Loading...</p>
        ) : memories.length === 0 ? (
          <p className="text-muted text-sm">
            {isSearchMode ? 'No memories match your query.' : 'No memories stored yet.'}
          </p>
        ) : (
          <div style={{ border: '1px solid var(--border)' }}>
            <table className="w-full">
              <tbody>
                {memories.map(memory => (
                  <MemoryRow key={memory.id} memory={memory} onDelete={handleDelete} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
