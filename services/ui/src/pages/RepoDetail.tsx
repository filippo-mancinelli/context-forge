import { useCallback, useEffect, useMemo, useState } from 'react'
import { ChevronRight, FileCode2, Folder, GitBranch, Loader2, Search, Sigma } from 'lucide-react'
import { Link, useParams } from 'react-router-dom'
import { api, type RepoSearchResult, type RepoStats } from '../lib/api'

function formatBytes(bytes?: number) {
  if (!bytes) return '—'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function snippet(content: string, max = 220) {
  const flat = content.replace(/\s+/g, ' ').trim()
  return flat.length > max ? `${flat.slice(0, max)}...` : flat
}

export default function RepoDetail() {
  const { repoName: encodedRepoName } = useParams()
  const repoName = encodedRepoName ? decodeURIComponent(encodedRepoName) : ''

  const [stats, setStats] = useState<RepoStats | null>(null)
  const [path, setPath] = useState('')
  const [entries, setEntries] = useState<{ name: string; type: string; size?: number; path: string }[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [searching, setSearching] = useState(false)
  const [results, setResults] = useState<RepoSearchResult[]>([])

  const loadStats = useCallback(async () => {
    if (!repoName) return
    const data = await api.repos.stats(repoName)
    setStats(data)
  }, [repoName])

  const loadFiles = useCallback(
    async (nextPath: string) => {
      if (!repoName) return
      const data = await api.repos.files(repoName, nextPath)
      setPath(data.path)
      setEntries(data.entries)
    },
    [repoName]
  )

  useEffect(() => {
    let mounted = true
    async function load() {
      setLoading(true)
      try {
        await Promise.all([loadStats(), loadFiles('')])
        if (!mounted) return
        setError(null)
      } catch (e) {
        if (!mounted) return
        setError(String(e))
      } finally {
        if (mounted) setLoading(false)
      }
    }
    load()
    return () => {
      mounted = false
    }
  }, [loadFiles, loadStats])

  const breadcrumbs = useMemo(() => {
    if (!path) return []
    const pieces = path.split('/').filter(Boolean)
    return pieces.map((part, idx) => ({
      label: part,
      value: pieces.slice(0, idx + 1).join('/'),
    }))
  }, [path])

  const searchScoped = async () => {
    if (!query.trim() || !repoName) return
    setSearching(true)
    try {
      const data = await api.repos.search(query.trim(), [repoName], 20)
      setResults(data.results)
    } finally {
      setSearching(false)
    }
  }

  return (
    <div className="p-8 space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-xs text-gray-500 mb-1"><Link to="/repos" className="hover:text-gray-300">Repositories</Link> / <span className="text-gray-300">{repoName}</span></p>
          <h1 className="text-xl font-semibold text-white flex items-center gap-2">
            <GitBranch className="w-5 h-5 text-indigo-400" />
            {repoName}
          </h1>
        </div>
      </div>

      {error && <div className="p-4 bg-red-500/10 border border-red-500/30 rounded-xl text-sm text-red-400">{error}</div>}

      {loading ? (
        <div className="flex items-center justify-center h-40 text-gray-500">
          <Loader2 className="w-5 h-5 mr-2 animate-spin" />
          Loading repository...
        </div>
      ) : (
        <>
          {stats && (
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
              <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
                <p className="text-xs text-gray-500 uppercase tracking-wide">Status</p>
                <p className="text-lg text-white mt-1">{stats.repo.status}</p>
              </div>
              <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
                <p className="text-xs text-gray-500 uppercase tracking-wide">Language</p>
                <p className="text-lg text-white mt-1">{stats.repo.language}</p>
              </div>
              <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
                <p className="text-xs text-gray-500 uppercase tracking-wide">Chunks</p>
                <p className="text-lg text-white mt-1">{stats.repo.total_chunks.toLocaleString()}</p>
              </div>
              <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
                <p className="text-xs text-gray-500 uppercase tracking-wide">Last Indexed</p>
                <p className="text-sm text-white mt-1">{stats.repo.last_indexed_at ? new Date(stats.repo.last_indexed_at).toLocaleString() : '—'}</p>
              </div>
            </div>
          )}

          <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
            <div className="xl:col-span-2 bg-gray-900 border border-gray-800 rounded-xl p-4">
              <div className="flex items-center justify-between mb-3">
                <h2 className="text-sm text-white font-medium">File Tree</h2>
                <div className="text-xs text-gray-500 font-mono">
                  <button className="hover:text-gray-300" onClick={() => loadFiles('')}>root</button>
                  {breadcrumbs.map(crumb => (
                    <span key={crumb.value}>
                      {' / '}
                      <button className="hover:text-gray-300" onClick={() => loadFiles(crumb.value)}>{crumb.label}</button>
                    </span>
                  ))}
                </div>
              </div>
              <div className="space-y-1 max-h-[420px] overflow-auto scrollbar-thin pr-1">
                {entries.map(entry => (
                  <button
                    key={entry.path}
                    onClick={() => entry.type === 'directory' && loadFiles(entry.path)}
                    className="w-full text-left flex items-center justify-between px-3 py-2 rounded-lg hover:bg-gray-800 transition-colors"
                  >
                    <span className="inline-flex items-center gap-2 text-sm text-gray-200">
                      {entry.type === 'directory' ? <Folder className="w-4 h-4 text-indigo-400" /> : <FileCode2 className="w-4 h-4 text-gray-500" />}
                      {entry.name}
                    </span>
                    <span className="text-xs text-gray-500 inline-flex items-center gap-2">
                      {entry.type === 'directory' ? <ChevronRight className="w-3 h-3" /> : formatBytes(entry.size)}
                    </span>
                  </button>
                ))}
                {entries.length === 0 && <p className="text-sm text-gray-500">No files in this folder.</p>}
              </div>
            </div>

            <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
              <h2 className="text-sm text-white font-medium inline-flex items-center gap-2 mb-3">
                <Sigma className="w-4 h-4 text-indigo-400" />
                Chunk Preview
              </h2>
              {stats && (
                <div className="space-y-2">
                  {stats.chunk_types.map(chunk => {
                    const pct = stats.repo.total_chunks > 0 ? (chunk.count / stats.repo.total_chunks) * 100 : 0
                    return (
                      <div key={chunk.chunk_type}>
                        <div className="flex items-center justify-between text-xs text-gray-400">
                          <span>{chunk.chunk_type}</span>
                          <span>{pct.toFixed(1)}%</span>
                        </div>
                        <div className="h-1.5 bg-gray-800 rounded-full overflow-hidden">
                          <div className="h-full bg-indigo-500" style={{ width: `${pct}%` }} />
                        </div>
                      </div>
                    )
                  })}
                  <div className="pt-2 border-t border-gray-800">
                    <p className="text-xs text-gray-500 mb-2">Top extensions</p>
                    {stats.by_extension.map(ext => (
                      <p key={ext.extension} className="text-xs text-gray-400">{ext.extension} · {ext.count} chunks</p>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>

          <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
            <h2 className="text-sm text-white font-medium mb-3">Search scoped to this repository</h2>
            <div className="flex gap-2 mb-3">
              <div className="flex-1 relative">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500" />
                <input
                  type="text"
                  value={query}
                  onChange={e => setQuery(e.target.value)}
                  onKeyDown={e => e.key === 'Enter' && searchScoped()}
                  placeholder={`Search only in ${repoName}`}
                  className="w-full pl-9 pr-4 py-2 text-sm bg-gray-950 border border-gray-700 rounded-lg text-gray-200 placeholder-gray-600 focus:outline-none focus:ring-2 focus:ring-indigo-500/50 focus:border-indigo-500"
                />
              </div>
              <button
                onClick={searchScoped}
                disabled={searching || !query.trim()}
                className="px-4 py-2 text-sm text-white bg-indigo-600 hover:bg-indigo-500 rounded-lg transition-colors disabled:opacity-50"
              >
                {searching ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Search'}
              </button>
            </div>
            <div className="space-y-2">
              {results.map((result, idx) => (
                <div key={`${result.file_path}-${idx}`} className="border border-gray-800 rounded-lg p-3">
                  <p className="text-xs text-gray-500 font-mono">{result.file_path} · {result.chunk_type} · score {result.score.toFixed(3)}</p>
                  <p className="text-sm text-gray-300 mt-1">{snippet(result.content)}</p>
                </div>
              ))}
              {results.length === 0 && <p className="text-sm text-gray-500">No scoped results yet.</p>}
            </div>
          </div>
        </>
      )}
    </div>
  )
}
