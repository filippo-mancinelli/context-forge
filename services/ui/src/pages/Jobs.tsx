import { useEffect, useState, useCallback } from 'react'
import { Activity, Loader2, CheckCircle, AlertCircle, Clock } from 'lucide-react'
import { api, type Job } from '../lib/api'

function StatusBadge({ status }: { status: Job['status'] }) {
  const map: Record<string, { label: string; className: string; icon: React.ReactNode }> = {
    done: {
      label: 'Done',
      className: 'bg-emerald-500/15 text-emerald-400 ring-1 ring-emerald-500/30',
      icon: <CheckCircle className="w-3 h-3" />,
    },
    running: {
      label: 'Running',
      className: 'bg-indigo-500/15 text-indigo-400 ring-1 ring-indigo-500/30',
      icon: <Loader2 className="w-3 h-3 animate-spin" />,
    },
    pending: {
      label: 'Pending',
      className: 'bg-yellow-500/15 text-yellow-400 ring-1 ring-yellow-500/30',
      icon: <Clock className="w-3 h-3" />,
    },
    error: {
      label: 'Error',
      className: 'bg-red-500/15 text-red-400 ring-1 ring-red-500/30',
      icon: <AlertCircle className="w-3 h-3" />,
    },
  }
  const { label, className, icon } = map[status] ?? map.pending
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${className}`}>
      {icon}
      {label}
    </span>
  )
}

function duration(created: string, updated: string) {
  const ms = new Date(updated).getTime() - new Date(created).getTime()
  if (ms < 1000) return `${ms}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
  return `${(ms / 60000).toFixed(1)}m`
}

export default function Jobs() {
  const [jobs, setJobs] = useState<Job[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const data = await api.jobs.list(100)
      setJobs(data.jobs)
      setError(null)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
    const interval = setInterval(load, 3000)
    return () => clearInterval(interval)
  }, [load])

  const active = jobs.filter(j => j.status === 'running' || j.status === 'pending').length

  return (
    <div className="p-8">
      <div className="mb-8">
        <h1 className="text-xl font-semibold text-white flex items-center gap-2">
          <Activity className="w-5 h-5 text-indigo-400" />
          Async Jobs
        </h1>
        <p className="text-sm text-gray-500 mt-1">
          {active > 0 ? `${active} active` : 'No active jobs'} · {jobs.length} total
        </p>
      </div>

      <div className="mb-6 p-4 bg-gray-900 border border-gray-800 rounded-xl">
        <p className="text-xs text-gray-500 mb-1 font-medium">About Async Jobs</p>
        <p className="text-xs text-gray-400">
          Use <code className="font-mono text-amber-300">job_submit(url, payload)</code> in your agent to call slow HTTP endpoints
          without MCP timeouts. Poll with <code className="font-mono text-amber-300">job_status(job_id)</code> and retrieve with{' '}
          <code className="font-mono text-amber-300">job_result(job_id)</code>.
        </p>
      </div>

      {error && (
        <div className="mb-6 p-4 bg-red-500/10 border border-red-500/30 rounded-xl text-sm text-red-400">{error}</div>
      )}

      {loading ? (
        <div className="flex items-center justify-center h-48 text-gray-600">
          <Loader2 className="w-5 h-5 animate-spin mr-2" />
          Loading…
        </div>
      ) : jobs.length === 0 ? (
        <div className="text-center py-20 text-gray-600">
          <Activity className="w-10 h-10 mx-auto mb-3 opacity-40" />
          <p className="text-sm">No jobs yet.</p>
        </div>
      ) : (
        <div className="bg-gray-900 rounded-xl border border-gray-800 overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-800 text-gray-500 text-xs uppercase tracking-wider">
                <th className="text-left px-5 py-3 font-medium">Job ID</th>
                <th className="text-left px-4 py-3 font-medium">Tool</th>
                <th className="text-left px-4 py-3 font-medium">Status</th>
                <th className="text-left px-4 py-3 font-medium">Duration</th>
                <th className="text-left px-4 py-3 font-medium">Created</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800">
              {jobs.map(job => (
                <tr key={job.id} className="hover:bg-gray-800/50 transition-colors">
                  <td className="px-5 py-3.5">
                    <code className="text-xs font-mono text-gray-500">{job.id.slice(0, 8)}…</code>
                  </td>
                  <td className="px-4 py-3.5">
                    <span className="text-xs font-mono text-amber-300">{job.tool}</span>
                  </td>
                  <td className="px-4 py-3.5">
                    <div>
                      <StatusBadge status={job.status} />
                      {job.error_message && (
                        <p className="text-xs text-red-400 mt-1 max-w-xs truncate">{job.error_message}</p>
                      )}
                    </div>
                  </td>
                  <td className="px-4 py-3.5 text-xs text-gray-500 font-mono">
                    {duration(job.created_at, job.updated_at)}
                  </td>
                  <td className="px-4 py-3.5 text-xs text-gray-500">
                    {new Date(job.created_at).toLocaleString(undefined, {
                      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
                    })}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
