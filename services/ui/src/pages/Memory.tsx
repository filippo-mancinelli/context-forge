import { useEffect, useState, useCallback } from 'react'
import { Brain, Search, Trash2, Loader2, X } from 'lucide-react'
import { api, type Memory as MemoryItem } from '../lib/api'

function MemoryCard({ memory, onDelete }: { memory: MemoryItem; onDelete: (id: string) => void }) {
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
    <div className="group bg-gray-900 border border-gray-800 rounded-xl p-4 hover:border-gray-700 transition-colors">
      <div className="flex items-start justify-between gap-3">
        <p className="text-sm text-gray-200 leading-relaxed flex-1">{memory.memory}</p>
        <button
          onClick={handleDelete}
          disabled={deleting}
          className="opacity-0 group-hover:opacity-100 flex-shrink-0 p-1 text-gray-600 hover:text-red-400 transition-all"
          title="Delete memory"
        >
          {deleting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
        </button>
      </div>
      <div className="flex items-center gap-3 mt-2.5">
        {memory.score !== undefined && (
          <span className="text-xs text-indigo-400 font-mono bg-indigo-500/10 px-1.5 py-0.5 rounded">
            score {memory.score?.toFixed(3)}
          </span>
        )}
        {memory.metadata && Object.keys(memory.metadata).length > 0 && (
          <div className="flex gap-1.5 flex-wrap">
            {Object.entries(memory.metadata).slice(0, 4).map(([k, v]) => (
              <span key={k} className="text-xs text-gray-600 bg-gray-800 px-1.5 py-0.5 rounded font-mono">
                {k}: {String(v)}
              </span>
            ))}
          </div>
        )}
        {memory.created_at && (
          <span className="text-xs text-gray-600 ml-auto">
            {new Date(memory.created_at).toLocaleDateString()}
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
  const [error, setError] = useState<string | null>(null)

  const loadAll = useCallback(async () => {
    try {
      const data = await api.memory.list(100)
      setMemories(data.memories)
      setError(null)
    } catch (e) {
      setError(String(e))
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
      return loadAll()
    }
    setSearching(true)
    setIsSearchMode(true)
    try {
      const data = await api.memory.search(query.trim(), 30)
      setMemories(data.memories)
    } catch (e) {
      setError(String(e))
    } finally {
      setSearching(false)
    }
  }

  const handleClear = () => {
    setQuery('')
    setIsSearchMode(false)
    loadAll()
  }

  const handleDelete = async (id: string) => {
    await api.memory.delete(id)
    setMemories(prev => prev.filter(m => m.id !== id))
  }

  return (
    <div className="p-8">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-xl font-semibold text-white flex items-center gap-2">
            <Brain className="w-5 h-5 text-indigo-400" />
            Memory
          </h1>
          <p className="text-sm text-gray-500 mt-1">
            {isSearchMode ? `${memories.length} results for "${query}"` : `${memories.length} memories stored`}
          </p>
        </div>
      </div>

      <div className="flex gap-2 mb-6">
        <div className="flex-1 relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500" />
          <input
            type="text"
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleSearch()}
            placeholder="Semantic search across memories…"
            className="w-full pl-9 pr-4 py-2 text-sm bg-gray-900 border border-gray-700 rounded-lg text-gray-200 placeholder-gray-600 focus:outline-none focus:ring-2 focus:ring-indigo-500/50 focus:border-indigo-500"
          />
        </div>
        {isSearchMode && (
          <button
            onClick={handleClear}
            className="px-3 py-2 text-sm text-gray-400 bg-gray-900 border border-gray-700 rounded-lg hover:bg-gray-800 transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        )}
        <button
          onClick={handleSearch}
          disabled={searching}
          className="px-4 py-2 text-sm text-white bg-indigo-600 hover:bg-indigo-500 rounded-lg transition-colors disabled:opacity-50"
        >
          {searching ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Search'}
        </button>
      </div>

      {error && (
        <div className="mb-6 p-4 bg-red-500/10 border border-red-500/30 rounded-xl text-sm text-red-400">
          {error}
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center h-48 text-gray-600">
          <Loader2 className="w-5 h-5 animate-spin mr-2" />
          Loading…
        </div>
      ) : memories.length === 0 ? (
        <div className="text-center py-20 text-gray-600">
          <Brain className="w-10 h-10 mx-auto mb-3 opacity-40" />
          <p className="text-sm">{isSearchMode ? 'No memories match your query.' : 'No memories stored yet.'}</p>
          <p className="text-xs mt-1">Use <code className="font-mono text-gray-500">memory_add()</code> in your agent to store information.</p>
        </div>
      ) : (
        <div className="space-y-3">
          {memories.map(m => (
            <MemoryCard key={m.id} memory={m} onDelete={handleDelete} />
          ))}
        </div>
      )}
    </div>
  )
}
