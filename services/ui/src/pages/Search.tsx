import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, type Job, type Memory, type RepoRelationship, type RepoSearchResult } from '../lib/api'
import { Button, Input } from '../components/ui'

function snippet(content: string, max = 260) {
  const flat = content.replace(/\s+/g, ' ').trim()
  return flat.length > max ? `${flat.slice(0, max)}...` : flat
}

export default function Search() {
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [repoResults, setRepoResults] = useState<RepoSearchResult[]>([])
  const [memories, setMemories] = useState<Memory[]>([])
  const [jobMatches, setJobMatches] = useState<Job[]>([])
  const [relationships, setRelationships] = useState<RepoRelationship[]>([])
  const [hasSearched, setHasSearched] = useState(false)

  const grouped = useMemo(() => {
    const groups: Record<string, RepoSearchResult[]> = {}
    for (const item of repoResults) {
      if (!groups[item.repo_name]) groups[item.repo_name] = []
      groups[item.repo_name].push(item)
    }
    return Object.entries(groups).sort((a, b) => b[1].length - a[1].length)
  }, [repoResults])

  const runSearch = async () => {
    if (!query.trim()) return
    setLoading(true)
    setError(null)
    try {
      const [repoData, memoryData, jobsData, relationshipsData] = await Promise.all([
        api.repos.search(query.trim(), undefined, 30),
        api.memory.search(query.trim(), 10),
        api.jobs.list(100),
        api.repos.relationships(),
      ])

      const q = query.trim().toLowerCase()
      const matchedJobs = jobsData.jobs.filter(
        job =>
          job.id.toLowerCase().includes(q) ||
          job.tool.toLowerCase().includes(q) ||
          job.status.toLowerCase().includes(q) ||
          (job.error_message || '').toLowerCase().includes(q)
      )

      const relatedRepos = new Set(repoData.results.map(r => r.repo_name))
      const filteredRelationships = relationshipsData.relationships.filter(
        edge => relatedRepos.has(edge.repo_a) || relatedRepos.has(edge.repo_b)
      )

      setRepoResults(repoData.results)
      setMemories(memoryData.memories)
      setJobMatches(matchedJobs.slice(0, 8))
      setRelationships(filteredRelationships.slice(0, 10))
      setHasSearched(true)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="p-4 sm:p-8">
      <div className="page-wide">
        <div className="mb-6">
          <h1>Cross-Repo Search</h1>
          <p className="text-muted text-sm">Unified search across repositories, memories, and jobs.</p>
        </div>

        <div className="flex gap-2 mb-6">
          <Input
            className="flex-1"
            type="text"
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && runSearch()}
            placeholder="Search across repos, memory, jobs..."
          />
          <Button
            variant="primary"
            onClick={runSearch}
            disabled={loading || !query.trim()}
            loading={loading}
          >
            Search
          </Button>
        </div>

        {error && (
          <div
            style={{ border: '1px solid var(--danger)', color: 'var(--danger)' }}
            className="text-sm p-3 mb-4 bg-[#fef2f2]"
          >
            {error}
          </div>
        )}

        {!hasSearched ? null : (
          <div className="space-y-8">
            {/* Code Results */}
            <section>
              <h2 className="text-base font-semibold mb-3">
                Code results
                {repoResults.length > 0 && (
                  <span className="text-muted font-normal text-sm ml-2">({repoResults.length})</span>
                )}
              </h2>
              {grouped.length === 0 ? (
                <p className="text-muted text-sm">No code matches found.</p>
              ) : (
                <div className="space-y-4">
                  {grouped.map(([repoName, items]) => (
                    <div key={repoName} style={{ border: '1px solid var(--border)' }}>
                      <div
                        style={{ borderBottom: '1px solid var(--border)' }}
                        className="flex items-center justify-between px-3 py-2 bg-surface"
                      >
                        <span className="text-sm font-medium text-accent">{repoName}</span>
                        <Link
                          to={`/repos/${encodeURIComponent(repoName)}`}
                          className="text-xs text-muted hover:text-accent"
                        >
                          Open repo →
                        </Link>
                      </div>
                      <div>
                        {items.slice(0, 4).map((result, idx) => (
                          <div
                            key={`${result.file_path}-${idx}`}
                            style={{ borderBottom: '1px solid var(--border)' }}
                            className="px-3 py-2 last:border-b-0"
                          >
                            <p className="text-xs text-muted font-mono mb-1">
                              {result.file_path} · {result.chunk_type} · score {result.score.toFixed(3)}
                            </p>
                            <p className="text-sm">{snippet(result.content)}</p>
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </section>

            {/* Memory + Jobs */}
            {(memories.length > 0 || jobMatches.length > 0) && (
              <section>
                <h2 className="text-base font-semibold mb-3">Memory &amp; Jobs</h2>
                <div className="space-y-2">
                  {memories.slice(0, 4).map(memory => (
                    <div
                      key={memory.id}
                      style={{ border: '1px solid var(--border)' }}
                      className="px-3 py-2 text-sm"
                    >
                      {snippet(memory.memory, 140)}
                    </div>
                  ))}
                  {jobMatches.map(job => (
                    <div
                      key={job.id}
                      style={{ border: '1px solid var(--border)' }}
                      className="px-3 py-2 text-sm"
                    >
                      <code className="font-mono text-xs">{job.tool}</code>
                      <span className="text-muted text-xs ml-2">{job.status} · {job.id.slice(0, 8)}...</span>
                    </div>
                  ))}
                </div>
              </section>
            )}

            {/* Relationships */}
            {relationships.length > 0 && (
              <section>
                <h2 className="text-base font-semibold mb-3">Repository relationships</h2>
                <div style={{ border: '1px solid var(--border)' }} className="overflow-x-auto">
                  <table className="w-full min-w-[420px]">
                    <tbody>
                      {relationships.map(edge => (
                        <tr
                          key={`${edge.repo_a}-${edge.repo_b}`}
                          style={{ borderBottom: '1px solid var(--border)' }}
                          className="last:border-b-0"
                        >
                          <td className="px-3 py-2 text-sm font-mono">
                            {edge.repo_a} → {edge.repo_b}
                          </td>
                          <td className="px-3 py-2 text-sm text-muted">
                            similarity {edge.similarity.toFixed(3)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
