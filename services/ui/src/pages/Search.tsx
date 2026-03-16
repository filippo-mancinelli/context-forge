import { useMemo, useState } from 'react'
import { ArrowRightLeft, Brain, Loader2, Search as SearchIcon, Sparkles } from 'lucide-react'
import { Link } from 'react-router-dom'
import { api, type Job, type Memory, type RepoRelationship, type RepoSearchResult } from '../lib/api'

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
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="p-8 space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-white flex items-center gap-2">
          <Sparkles className="w-5 h-5 text-indigo-400" />
          Cross-Repo Search
        </h1>
        <p className="text-sm text-gray-500 mt-1">Unified search across repositories, memories and jobs.</p>
      </div>

      <div className="flex gap-2">
        <div className="flex-1 relative">
          <SearchIcon className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500" />
          <input
            type="text"
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && runSearch()}
            placeholder="Search across repos + memory + jobs..."
            className="w-full pl-9 pr-4 py-2 text-sm bg-gray-900 border border-gray-700 rounded-lg text-gray-200 placeholder-gray-600 focus:outline-none focus:ring-2 focus:ring-indigo-500/50 focus:border-indigo-500"
          />
        </div>
        <button
          onClick={runSearch}
          disabled={loading || !query.trim()}
          className="px-4 py-2 text-sm text-white bg-indigo-600 hover:bg-indigo-500 rounded-lg transition-colors disabled:opacity-50"
        >
          {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Search'}
        </button>
      </div>

      {error && <div className="p-4 bg-red-500/10 border border-red-500/30 rounded-xl text-sm text-red-400">{error}</div>}

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        <div className="xl:col-span-2 bg-gray-900 border border-gray-800 rounded-xl p-4">
          <h2 className="text-sm text-white font-medium mb-3">Code Results by Repository</h2>
          <div className="space-y-4">
            {grouped.length === 0 && <p className="text-sm text-gray-500">Run a query to see grouped results.</p>}
            {grouped.map(([repoName, items]) => (
              <div key={repoName} className="border border-gray-800 rounded-xl p-3">
                <div className="flex items-center justify-between mb-2">
                  <p className="text-sm text-indigo-300 font-medium">{repoName}</p>
                  <Link to={`/repos/${encodeURIComponent(repoName)}`} className="text-xs text-gray-400 hover:text-gray-200">
                    Open repo
                  </Link>
                </div>
                <div className="space-y-2">
                  {items.slice(0, 4).map((result, idx) => (
                    <div key={`${result.file_path}-${idx}`} className="bg-gray-800/60 rounded-lg p-2">
                      <p className="text-xs text-gray-400 font-mono">
                        {result.file_path} · {result.chunk_type} · score {result.score.toFixed(3)}
                      </p>
                      <p className="text-xs text-gray-300 mt-1">{snippet(result.content)}</p>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="space-y-4">
          <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
            <h2 className="text-sm text-white font-medium mb-3 inline-flex items-center gap-2">
              <ArrowRightLeft className="w-4 h-4 text-indigo-400" />
              Relationship Graph
            </h2>
            <div className="space-y-2">
              {relationships.length === 0 && <p className="text-xs text-gray-500">No edges for current results.</p>}
              {relationships.map(edge => (
                <div key={`${edge.repo_a}-${edge.repo_b}`} className="text-xs text-gray-300 bg-gray-800/60 rounded-lg px-3 py-2">
                  <p className="font-mono text-gray-200">{edge.repo_a} <span className="text-gray-500">-&gt;</span> {edge.repo_b}</p>
                  <p className="text-gray-500">similarity {edge.similarity.toFixed(3)}</p>
                </div>
              ))}
            </div>
          </div>

          <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
            <h2 className="text-sm text-white font-medium mb-3 inline-flex items-center gap-2">
              <Brain className="w-4 h-4 text-indigo-400" />
              Memory + Jobs
            </h2>
            <div className="space-y-2">
              {memories.slice(0, 4).map(memory => (
                <div key={memory.id} className="text-xs text-gray-300 bg-gray-800/60 rounded-lg px-3 py-2">
                  {snippet(memory.memory, 140)}
                </div>
              ))}
              {jobMatches.map(job => (
                <div key={job.id} className="text-xs text-gray-300 bg-gray-800/60 rounded-lg px-3 py-2">
                  <p className="font-mono text-amber-300">{job.tool}</p>
                  <p className="text-gray-500">{job.status} · {job.id.slice(0, 8)}...</p>
                </div>
              ))}
              {memories.length === 0 && jobMatches.length === 0 && (
                <p className="text-xs text-gray-500">No memory/job match for this query.</p>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
