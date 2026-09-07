import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react'
import { Plus, Trash2, X } from 'lucide-react'
import { api, type Memory as MemoryItem } from '../lib/api'
import { Badge, Banner, Button, Card, Input, Select, Textarea, useConfirm, useToast } from '../components/ui'

const FETCH_LIMIT = 500
const UNTYPED = 'untyped'

function parseTagsInput(value: string): string[] {
  return [...new Set(value.split(',').map(t => t.trim()).filter(Boolean))]
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

function getType(memory: MemoryItem): string {
  const type = memory.metadata?.type
  return typeof type === 'string' && type.trim() ? type : UNTYPED
}

function getTags(memory: MemoryItem): string[] {
  const tags = memory.metadata?.tags
  if (!Array.isArray(tags)) return []
  return tags.filter((t): t is string => typeof t === 'string')
}

function getExtraMetadata(memory: MemoryItem): [string, unknown][] {
  if (!memory.metadata) return []
  return Object.entries(memory.metadata).filter(([key]) => key !== 'type' && key !== 'tags')
}

function MemoryRow({ memory, onDelete }: { memory: MemoryItem; onDelete: (id: string) => Promise<void> }) {
  const [deleting, setDeleting] = useState(false)
  const type = getType(memory)
  const tags = getTags(memory)
  const extra = getExtraMetadata(memory)

  const handleDelete = async () => {
    setDeleting(true)
    try {
      await onDelete(memory.id)
    } finally {
      setDeleting(false)
    }
  }

  return (
    <tr className="border-b border-border last:border-b-0 group">
      <td className="py-3 px-4 align-top min-w-0">
        <p className="text-sm break-words">{memory.memory}</p>
        {(type !== UNTYPED || tags.length > 0 || extra.length > 0) && (
          <div className="flex flex-wrap gap-1 mt-1.5 min-w-0">
            {type !== UNTYPED && <Badge variant="accent">{type}</Badge>}
            {tags.map(tag => (
              <Badge key={tag} variant="muted">{tag}</Badge>
            ))}
            {extra.slice(0, 3).map(([key, value]) => (
              <Badge key={key} variant="default" className="font-mono">
                {key}: {String(value)}
              </Badge>
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
  const confirm = useConfirm()
  const toast = useToast()
  const [memories, setMemories] = useState<MemoryItem[]>([])
  const [loading, setLoading] = useState(true)
  const [query, setQuery] = useState('')
  const [searching, setSearching] = useState(false)
  const [isSearchMode, setIsSearchMode] = useState(false)
  const [pageError, setPageError] = useState<string | null>(null)
  const [showAddForm, setShowAddForm] = useState(false)
  const [content, setContent] = useState('')
  const [typeInput, setTypeInput] = useState('')
  const [tagsInput, setTagsInput] = useState('')
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)
  const [createSuccess, setCreateSuccess] = useState(false)
  const [typeFilter, setTypeFilter] = useState('all')
  const [tagFilter, setTagFilter] = useState<Set<string>>(new Set())
  const [sortOrder, setSortOrder] = useState<'newest' | 'oldest'>('newest')

  const loadAll = useCallback(async () => {
    try {
      const data = await api.memory.list(FETCH_LIMIT)
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
      const data = await api.memory.search(query.trim(), 50)
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
    const ok = await confirm({
      title: 'Delete memory',
      message: 'Delete this memory? This cannot be undone.',
      confirmLabel: 'Delete',
      danger: true,
      onConfirm: () => api.memory.delete(id),
    })
    if (!ok) return
    toast.success('Memory deleted')
    setMemories(prev => prev.filter(m => m.id !== id))
  }

  const handleCreate = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const trimmedContent = content.trim()
    if (!trimmedContent) {
      setCreateError('Write the memory content before saving.')
      return
    }
    const metadata: Record<string, unknown> = {}
    const type = typeInput.trim()
    if (type) metadata.type = type
    const tags = parseTagsInput(tagsInput)
    if (tags.length > 0) metadata.tags = tags
    setCreating(true)
    setCreateError(null)
    setCreateSuccess(false)
    try {
      await api.memory.create({
        content: trimmedContent,
        metadata: Object.keys(metadata).length > 0 ? metadata : undefined,
        infer: false,
      })
      setContent('')
      setTypeInput('')
      setTagsInput('')
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

  const typeOptions = useMemo(() => {
    const counts = new Map<string, number>()
    for (const m of memories) {
      const type = getType(m)
      counts.set(type, (counts.get(type) || 0) + 1)
    }
    const sorted = [...counts.entries()].sort((a, b) => b[1] - a[1])
    return [
      { value: 'all', label: `All types (${memories.length})` },
      ...sorted.map(([type, count]) => ({
        value: type,
        label: `${type === UNTYPED ? 'Untyped' : type} (${count})`,
      })),
    ]
  }, [memories])

  const knownTypes = useMemo(() => {
    const types = new Set<string>()
    for (const m of memories) {
      const type = getType(m)
      if (type !== UNTYPED) types.add(type)
    }
    return [...types].sort()
  }, [memories])

  const allTags = useMemo(() => {
    const counts = new Map<string, number>()
    for (const m of memories) {
      for (const tag of getTags(m)) {
        counts.set(tag, (counts.get(tag) || 0) + 1)
      }
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1]).map(([tag]) => tag)
  }, [memories])

  const toggleFormTag = (tag: string) => {
    const tags = parseTagsInput(tagsInput)
    const next = tags.includes(tag) ? tags.filter(t => t !== tag) : [...tags, tag]
    setTagsInput(next.join(', '))
  }

  const toggleTag = (tag: string) => {
    setTagFilter(prev => {
      const next = new Set(prev)
      if (next.has(tag)) next.delete(tag)
      else next.add(tag)
      return next
    })
  }

  const visibleMemories = useMemo(() => {
    let list = memories
    if (typeFilter !== 'all') {
      list = list.filter(m => getType(m) === typeFilter)
    }
    if (tagFilter.size > 0) {
      list = list.filter(m => getTags(m).some(tag => tagFilter.has(tag)))
    }
    if (!isSearchMode) {
      list = [...list].sort((a, b) => {
        const da = a.created_at ? new Date(a.created_at).getTime() : 0
        const db = b.created_at ? new Date(b.created_at).getTime() : 0
        return sortOrder === 'newest' ? db - da : da - db
      })
    }
    return list
  }, [memories, typeFilter, tagFilter, isSearchMode, sortOrder])

  const filtersActive = typeFilter !== 'all' || tagFilter.size > 0

  return (
    <div className="p-4 sm:p-8">
      <div className="page-wide">
        <div className="mb-6 flex items-start justify-between gap-4">
          <div>
            <h1>Memory</h1>
            <p className="text-muted text-sm">
              {isSearchMode
                ? `${visibleMemories.length} result${visibleMemories.length === 1 ? '' : 's'} for "${query}"`
                : `Showing ${visibleMemories.length} of ${memories.length} stored memories`}
            </p>
          </div>
          <Button variant={showAddForm ? 'ghost' : 'primary'} onClick={() => setShowAddForm(v => !v)}>
            {showAddForm ? <><X className="w-3.5 h-3.5" /> Cancel</> : <><Plus className="w-3.5 h-3.5" /> Add memory</>}
          </Button>
        </div>

        {pageError && <Banner variant="danger" className="mb-4">{pageError}</Banner>}

        {/* Add memory form */}
        {showAddForm && (
          <section className="border border-border mb-6 p-4">
            <h2 className="text-base font-semibold mb-4">Add memory</h2>
            <form onSubmit={handleCreate} className="space-y-3">
              <Textarea
                label="Content"
                value={content}
                onChange={e => setContent(e.target.value)}
                rows={4}
                placeholder="The billing service now reads provider tokens from runtime settings."
              />
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                  <Input
                    label="Type (optional)"
                    value={typeInput}
                    onChange={e => setTypeInput(e.target.value)}
                    placeholder="decision"
                    list="memory-type-suggestions"
                  />
                  <datalist id="memory-type-suggestions">
                    {knownTypes.map(type => (
                      <option key={type} value={type} />
                    ))}
                  </datalist>
                </div>
                <Input
                  label="Tags (optional, comma-separated)"
                  value={tagsInput}
                  onChange={e => setTagsInput(e.target.value)}
                  placeholder="billing, backend"
                />
              </div>
              {allTags.length > 0 && (
                <div className="flex flex-wrap items-center gap-1.5">
                  {allTags.map(tag => {
                    const selected = parseTagsInput(tagsInput).includes(tag)
                    return (
                      <button key={tag} type="button" onClick={() => toggleFormTag(tag)}>
                        <Badge variant={selected ? 'accent' : 'muted'} className={selected ? 'ring-1 ring-accent' : ''}>
                          {tag}
                        </Badge>
                      </button>
                    )
                  })}
                </div>
              )}
              {createError && (
                <p className="text-sm text-danger">{createError}</p>
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
        )}

        {/* Search */}
        <div className="flex gap-2 mb-3">
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

        {/* Filters */}
        <div className="flex flex-wrap items-center gap-2 mb-2">
          <Select
            value={typeFilter}
            onValueChange={setTypeFilter}
            options={typeOptions}
            className="w-56"
          />
          {!isSearchMode && (
            <Select
              value={sortOrder}
              onValueChange={v => setSortOrder(v as 'newest' | 'oldest')}
              options={[
                { value: 'newest', label: 'Newest first' },
                { value: 'oldest', label: 'Oldest first' },
              ]}
              className="w-40"
            />
          )}
          {filtersActive && (
            <button
              onClick={() => { setTypeFilter('all'); setTagFilter(new Set()) }}
              className="text-xs text-muted hover:text-text underline"
            >
              Clear filters
            </button>
          )}
        </div>

        {allTags.length > 0 && (
          <div className="flex flex-wrap gap-1.5 mb-4">
            {allTags.map(tag => {
              const active = tagFilter.has(tag)
              return (
                <button key={tag} onClick={() => toggleTag(tag)} type="button">
                  <Badge variant={active ? 'accent' : 'muted'} className={active ? 'ring-1 ring-accent' : ''}>
                    {tag}
                  </Badge>
                </button>
              )
            })}
          </div>
        )}

        {/* Memory list */}
        {loading ? (
          <p className="text-muted text-sm">Loading...</p>
        ) : visibleMemories.length === 0 ? (
          <p className="text-muted text-sm">
            {isSearchMode ? 'No memories match your query.' : filtersActive ? 'No memories match these filters.' : 'No memories stored yet.'}
          </p>
        ) : (
          <Card className="overflow-x-auto max-w-5xl">
            <table className="w-full">
              <tbody>
                {visibleMemories.map(memory => (
                  <MemoryRow key={memory.id} memory={memory} onDelete={handleDelete} />
                ))}
              </tbody>
            </table>
          </Card>
        )}
      </div>
    </div>
  )
}
