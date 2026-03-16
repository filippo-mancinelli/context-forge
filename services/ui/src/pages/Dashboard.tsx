import { useEffect, useMemo, useState } from 'react'
import { Activity, ArrowRight, Brain, Database, GitBranch, HeartPulse, Search, Server, Loader2 } from 'lucide-react'
import { Link } from 'react-router-dom'
import { api, type Job, type Memory, type Repo } from '../lib/api'

type ServiceStatus = 'healthy' | 'degraded' | 'offline'

function relativeTime(iso?: string) {
  if (!iso) return 'never'
  const ms = Date.now() - new Date(iso).getTime()
  const minutes = Math.floor(ms / 60000)
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  return `${Math.floor(hours / 24)}d ago`
}

export default function Dashboard() {
  const [repos, setRepos] = useState<Repo[]>([])
  const [jobs, setJobs] = useState<Job[]>([])
  const [memories, setMemories] = useState<Memory[]>([])
  const [loading, setLoading] = useState(true)
  const [apiOnline, setApiOnline] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let mounted = true
    async function load() {
      try {
        const [health, reposData, jobsData, memoryData] = await Promise.all([
          api.health(),
          api.repos.list(),
          api.jobs.list(8),
          api.memory.list(8),
        ])
        if (!mounted) return
        setApiOnline(health.status === 'ok')
        setRepos(reposData)
        setJobs(jobsData.jobs)
        setMemories(memoryData.memories)
        setError(null)
      } catch (e) {
        if (!mounted) return
        setError(String(e))
      } finally {
        if (mounted) setLoading(false)
      }
    }

    load()
    const id = setInterval(load, 8000)
    return () => {
      mounted = false
      clearInterval(id)
    }
  }, [])

  const totalChunks = useMemo(() => repos.reduce((sum, repo) => sum + repo.total_chunks, 0), [repos])
  const indexed = useMemo(() => repos.filter(repo => repo.status === 'indexed').length, [repos])
  const indexing = useMemo(() => repos.filter(repo => repo.status === 'indexing').length, [repos])
  const errors = useMemo(() => repos.filter(repo => repo.status === 'error').length, [repos])
  const lastIndexedAt = useMemo(
    () =>
      repos
        .map(r => r.last_indexed_at)
        .filter(Boolean)
        .sort()
        .slice(-1)[0],
    [repos]
  )

  const dbStatus: ServiceStatus = repos.length > 0 || error === null ? 'healthy' : 'offline'
  const indexerStatus: ServiceStatus = errors > 0 ? 'degraded' : indexing > 0 ? 'healthy' : 'healthy'
  const apiStatus: ServiceStatus = apiOnline ? 'healthy' : 'offline'

  const statusStyles: Record<ServiceStatus, string> = {
    healthy: 'text-emerald-400 bg-emerald-500/10 ring-emerald-500/30',
    degraded: 'text-yellow-400 bg-yellow-500/10 ring-yellow-500/30',
    offline: 'text-red-400 bg-red-500/10 ring-red-500/30',
  }

  return (
    <div className="p-8 space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-white flex items-center gap-2">
          <HeartPulse className="w-6 h-6 text-indigo-400" />
          Unified Dashboard
        </h1>
        <p className="text-sm text-gray-500 mt-1">Live view across services, repositories, memory and jobs.</p>
      </div>

      {error && (
        <div className="p-4 bg-red-500/10 border border-red-500/30 rounded-xl text-sm text-red-400">{error}</div>
      )}

      {loading ? (
        <div className="flex items-center justify-center h-40 text-gray-500">
          <Loader2 className="w-5 h-5 mr-2 animate-spin" />
          Loading dashboard...
        </div>
      ) : (
        <>
          <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
            {[
              { label: 'API', icon: Server, value: apiStatus },
              { label: 'Database', icon: Database, value: dbStatus },
              { label: 'Indexer', icon: Activity, value: indexerStatus },
            ].map(({ label, icon: Icon, value }) => (
              <div key={label} className="bg-gray-900 border border-gray-800 rounded-xl p-4">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2 text-gray-300">
                    <Icon className="w-4 h-4 text-indigo-400" />
                    <span className="text-sm">{label}</span>
                  </div>
                  <span className={`text-xs px-2 py-0.5 rounded-full ring-1 capitalize ${statusStyles[value]}`}>{value}</span>
                </div>
              </div>
            ))}
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
            <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
              <p className="text-xs text-gray-500 uppercase tracking-wide">Indexed Repos</p>
              <p className="text-2xl text-white font-semibold mt-1">{indexed}/{repos.length}</p>
            </div>
            <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
              <p className="text-xs text-gray-500 uppercase tracking-wide">Total Chunks</p>
              <p className="text-2xl text-white font-semibold mt-1">{totalChunks.toLocaleString()}</p>
            </div>
            <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
              <p className="text-xs text-gray-500 uppercase tracking-wide">Active Jobs</p>
              <p className="text-2xl text-white font-semibold mt-1">
                {jobs.filter(j => j.status === 'running' || j.status === 'pending').length}
              </p>
            </div>
            <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
              <p className="text-xs text-gray-500 uppercase tracking-wide">Last Indexing</p>
              <p className="text-2xl text-white font-semibold mt-1">{relativeTime(lastIndexedAt)}</p>
            </div>
          </div>

          <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
            <div className="xl:col-span-2 bg-gray-900 border border-gray-800 rounded-xl p-4">
              <div className="flex items-center justify-between mb-3">
                <h2 className="text-sm text-white font-medium">Recent Activity</h2>
                <Link to="/jobs" className="text-xs text-indigo-400 hover:text-indigo-300 inline-flex items-center gap-1">
                  View jobs <ArrowRight className="w-3.5 h-3.5" />
                </Link>
              </div>
              <div className="space-y-2">
                {jobs.slice(0, 6).map(job => (
                  <div key={job.id} className="text-xs text-gray-400 border border-gray-800 rounded-lg px-3 py-2">
                    <span className="text-amber-300 font-mono">{job.tool}</span> · {job.status} · {relativeTime(job.updated_at)}
                  </div>
                ))}
                {jobs.length === 0 && <p className="text-xs text-gray-500">No jobs yet.</p>}
              </div>
            </div>

            <div className="bg-gray-900 border border-gray-800 rounded-xl p-4">
              <h2 className="text-sm text-white font-medium mb-3">Quick Actions</h2>
              <div className="space-y-2">
                <Link
                  to="/search"
                  className="flex items-center justify-between text-sm text-gray-200 bg-gray-800 hover:bg-gray-700 rounded-lg px-3 py-2 transition-colors"
                >
                  <span className="inline-flex items-center gap-2"><Search className="w-4 h-4 text-indigo-400" />Search all repos</span>
                  <ArrowRight className="w-4 h-4 text-gray-500" />
                </Link>
                <Link
                  to="/memory"
                  className="flex items-center justify-between text-sm text-gray-200 bg-gray-800 hover:bg-gray-700 rounded-lg px-3 py-2 transition-colors"
                >
                  <span className="inline-flex items-center gap-2"><Brain className="w-4 h-4 text-indigo-400" />Recent memories</span>
                  <ArrowRight className="w-4 h-4 text-gray-500" />
                </Link>
                <Link
                  to="/repos"
                  className="flex items-center justify-between text-sm text-gray-200 bg-gray-800 hover:bg-gray-700 rounded-lg px-3 py-2 transition-colors"
                >
                  <span className="inline-flex items-center gap-2"><GitBranch className="w-4 h-4 text-indigo-400" />Repo drill-down</span>
                  <ArrowRight className="w-4 h-4 text-gray-500" />
                </Link>
              </div>
              <div className="mt-4 border-t border-gray-800 pt-3">
                <p className="text-xs text-gray-500 mb-2">New memories</p>
                {memories.slice(0, 3).map(memory => (
                  <p key={memory.id} className="text-xs text-gray-400 mb-1 line-clamp-2">{memory.memory}</p>
                ))}
                {memories.length === 0 && <p className="text-xs text-gray-500">No memory events yet.</p>}
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
