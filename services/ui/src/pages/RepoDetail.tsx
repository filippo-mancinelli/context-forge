import { useCallback, useEffect, useMemo, useState } from 'react'
import { ChevronRight, FileCode2, Folder } from 'lucide-react'
import { Link, useParams } from 'react-router-dom'
import { api, type RepoSearchResult, type RepoStats } from '../lib/api'
import { Button, Input, Badge } from '../components/ui'

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
  const [filesAvailable, setFilesAvailable] = useState(true)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [filesError, setFilesError] = useState<string | null>(null)
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
      setFilesAvailable(data.available !== false)
    },
    [repoName]
  )

  useEffect(() => {
    let mounted = true
    async function load() {
      setLoading(true)
      // Load stats and files independently so a failure in one (e.g. the
      // working tree isn't cached on this server) doesn't blank the whole page.
      const [statsRes, filesRes] = await Promise.allSettled([loadStats(), loadFiles('')])
      if (!mounted) return
      setError(statsRes.status === 'rejected' ? String(statsRes.reason) : null)
      setFilesError(filesRes.status === 'rejected' ? String(filesRes.reason) : null)
      setLoading(false)
    }
    load()
    return () => { mounted = false }
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
    <div className="p-4 sm:p-8">
      <div className="page-content">
        <div className="mb-6">
          <p className="text-xs text-muted mb-2">
            <Link to="/repos" className="hover:text-accent">Repositories</Link>
            {' / '}
            <span className="text-text">{repoName}</span>
          </p>
          <h1>{repoName}</h1>
        </div>

        {error && (
          <div
            style={{ border: '1px solid var(--danger)', color: 'var(--danger)' }}
            className="text-sm p-3 mb-4 bg-[#fef2f2]"
          >
            {error}
          </div>
        )}

        {loading ? (
          <p className="text-muted text-sm">Loading repository...</p>
        ) : (
          <>
            {/* Stats strip */}
            {stats && (
              <div
                style={{ border: '1px solid var(--border)' }}
                className="grid grid-cols-2 md:grid-cols-4 mb-6"
              >
                {[
                  { label: 'Status', value: <Badge variant={stats.repo.status === 'indexed' ? 'success' : stats.repo.status === 'error' ? 'danger' : 'warning'}>{stats.repo.status}</Badge> },
                  { label: 'Language', value: stats.repo.language },
                  { label: 'Chunks', value: stats.repo.total_chunks.toLocaleString() },
                  { label: 'Last indexed', value: stats.repo.last_indexed_at ? new Date(stats.repo.last_indexed_at).toLocaleDateString() : '—' },
                ].map(({ label, value }) => (
                  <div
                    key={label}
                    style={{ borderRight: '1px solid var(--border)' }}
                    className="p-4 last:border-r-0"
                  >
                    <p className="text-xs text-muted uppercase tracking-wide mb-1">{label}</p>
                    <div className="text-sm font-medium">{value}</div>
                  </div>
                ))}
              </div>
            )}

            <div className="grid grid-cols-1 xl:grid-cols-3 gap-6 mb-6">
              {/* File tree */}
              <div
                style={{ border: '1px solid var(--border)' }}
                className="xl:col-span-2"
              >
                <div
                  style={{ borderBottom: '1px solid var(--border)' }}
                  className="px-4 py-2.5 flex items-center justify-between bg-surface"
                >
                  <span className="text-sm font-medium">Files</span>
                  <div className="text-xs text-muted font-mono flex items-center gap-1">
                    <button className="hover:text-accent" onClick={() => loadFiles('')}>root</button>
                    {breadcrumbs.map(crumb => (
                      <span key={crumb.value} className="flex items-center gap-1">
                        <ChevronRight className="w-3 h-3" />
                        <button className="hover:text-accent" onClick={() => loadFiles(crumb.value)}>
                          {crumb.label}
                        </button>
                      </span>
                    ))}
                  </div>
                </div>
                <div className="max-h-80 overflow-auto scrollbar-thin">
                  {entries.map(entry => (
                    <button
                      key={entry.path}
                      onClick={() => entry.type === 'directory' && loadFiles(entry.path)}
                      style={{ borderBottom: '1px solid var(--border)' }}
                      className="w-full text-left flex items-center justify-between gap-2 px-4 py-2 hover:bg-surface transition-colors last:border-b-0"
                    >
                      <span className="inline-flex items-center gap-2 text-sm min-w-0">
                        {entry.type === 'directory'
                          ? <Folder className="w-3.5 h-3.5 text-accent flex-shrink-0" />
                          : <FileCode2 className="w-3.5 h-3.5 text-muted flex-shrink-0" />}
                        <span className="truncate">{entry.name}</span>
                      </span>
                      <span className="text-xs text-muted flex-shrink-0">
                        {entry.type === 'directory' ? '/' : formatBytes(entry.size)}
                      </span>
                    </button>
                  ))}
                  {entries.length === 0 && (
                    <p className="text-sm text-muted px-4 py-3">
                      {filesError
                        ? 'Could not load files for this repository.'
                        : !filesAvailable
                          ? 'Source files are not cached on this server. The repository is indexed, but its working tree is not available here.'
                          : 'No files in this folder.'}
                    </p>
                  )}
                </div>
              </div>

              {/* Chunk breakdown */}
              <div style={{ border: '1px solid var(--border)' }}>
                <div
                  style={{ borderBottom: '1px solid var(--border)' }}
                  className="px-4 py-2.5 bg-surface"
                >
                  <span className="text-sm font-medium">Chunk breakdown</span>
                </div>
                <div className="p-4">
                  {stats && (
                    <div className="space-y-3">
                      {stats.chunk_types.map(chunk => {
                        const pct = stats.repo.total_chunks > 0
                          ? (chunk.count / stats.repo.total_chunks) * 100
                          : 0
                        return (
                          <div key={chunk.chunk_type}>
                            <div className="flex items-center justify-between text-xs text-muted mb-1">
                              <span>{chunk.chunk_type}</span>
                              <span>{pct.toFixed(1)}%</span>
                            </div>
                            <div
                              style={{ background: 'var(--border)', height: '4px' }}
                              className="w-full overflow-hidden"
                            >
                              <div
                                style={{ background: 'var(--accent)', width: `${pct}%`, height: '100%' }}
                              />
                            </div>
                          </div>
                        )
                      })}
                      {stats.by_extension.length > 0 && (
                        <div style={{ borderTop: '1px solid var(--border)' }} className="pt-3">
                          <p className="text-xs text-muted mb-2 uppercase tracking-wide">Top extensions</p>
                          {stats.by_extension.map(ext => (
                            <p key={ext.extension} className="text-xs text-muted">
                              <code className="font-mono">{ext.extension}</code> · {ext.count} chunks
                            </p>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </div>
            </div>

            {/* Scoped search */}
            <section style={{ border: '1px solid var(--border)' }}>
              <div
                style={{ borderBottom: '1px solid var(--border)' }}
                className="px-4 py-2.5 bg-surface"
              >
                <span className="text-sm font-medium">Search this repository</span>
              </div>
              <div className="p-4">
                <div className="flex gap-2 mb-4">
                  <Input
                    className="flex-1"
                    type="text"
                    value={query}
                    onChange={e => setQuery(e.target.value)}
                    onKeyDown={e => e.key === 'Enter' && searchScoped()}
                    placeholder={`Search in ${repoName}...`}
                  />
                  <Button
                    variant="primary"
                    onClick={searchScoped}
                    disabled={searching || !query.trim()}
                    loading={searching}
                  >
                    Search
                  </Button>
                </div>
                {results.length === 0 ? (
                  <p className="text-sm text-muted">No results yet.</p>
                ) : (
                  <div className="space-y-3">
                    {results.map((result, idx) => (
                      <div
                        key={`${result.file_path}-${idx}`}
                        style={{ border: '1px solid var(--border)' }}
                        className="p-3"
                      >
                        <p className="text-xs text-muted font-mono mb-1 break-all">
                          {result.file_path} · {result.chunk_type} · score {result.score.toFixed(3)}
                        </p>
                        <p className="text-sm break-words">{snippet(result.content)}</p>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </section>
          </>
        )}
      </div>
    </div>
  )
}
