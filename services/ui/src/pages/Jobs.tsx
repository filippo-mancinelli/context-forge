import { useEffect, useState, useCallback } from 'react'
import { api, type Job } from '../lib/api'
import { Badge } from '../components/ui'
import { Table, Thead, Tbody, Tr, Th, Td } from '../components/ui'

function jobBadgeVariant(status: Job['status']) {
  const map: Record<Job['status'], 'success' | 'accent' | 'warning' | 'danger'> = {
    done: 'success',
    running: 'accent',
    pending: 'warning',
    error: 'danger',
  }
  return map[status] ?? 'default'
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
    <div className="p-4 sm:p-8">
      <div className="page-wide">
        <div className="mb-6">
          <h1>Async Jobs</h1>
          <p className="text-muted text-sm">
            {active > 0 ? `${active} active` : 'No active jobs'} &middot; {jobs.length} total
          </p>
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
          <p className="text-muted text-sm">Loading...</p>
        ) : jobs.length === 0 ? (
          <p className="text-muted text-sm">No jobs yet.</p>
        ) : (
          <>
          {/* Mobile: stacked cards */}
          <div className="space-y-3 md:hidden">
            {jobs.map(job => (
              <div
                key={job.id}
                style={{ border: '1px solid var(--border)' }}
                className="p-3"
              >
                <div className="flex items-start justify-between gap-2">
                  <code className="font-mono text-xs break-all">{job.tool}</code>
                  <Badge variant={jobBadgeVariant(job.status)}>{job.status}</Badge>
                </div>
                {job.error_message && (
                  <p className="text-xs text-danger mt-1 break-words">{job.error_message}</p>
                )}
                <div className="flex flex-wrap gap-x-4 gap-y-1 mt-2 text-xs text-muted">
                  <span className="font-mono">{job.id.slice(0, 8)}&hellip;</span>
                  <span>{duration(job.created_at, job.updated_at)}</span>
                  <span>
                    {new Date(job.created_at).toLocaleString(undefined, {
                      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
                    })}
                  </span>
                </div>
              </div>
            ))}
          </div>

          {/* Desktop: table */}
          <div style={{ border: '1px solid var(--border)' }} className="hidden md:block">
            <Table>
              <Thead>
                <Tr>
                  <Th>Job ID</Th>
                  <Th>Tool</Th>
                  <Th>Status</Th>
                  <Th>Duration</Th>
                  <Th>Created</Th>
                </Tr>
              </Thead>
              <Tbody>
                {jobs.map(job => (
                  <Tr key={job.id}>
                    <Td>
                      <code className="font-mono text-xs text-muted">{job.id.slice(0, 8)}&hellip;</code>
                    </Td>
                    <Td>
                      <code className="font-mono text-xs">{job.tool}</code>
                    </Td>
                    <Td>
                      <Badge variant={jobBadgeVariant(job.status)}>
                        {job.status}
                      </Badge>
                      {job.error_message && (
                        <p className="text-xs text-danger mt-1 max-w-xs truncate">{job.error_message}</p>
                      )}
                    </Td>
                    <Td className="text-xs text-muted font-mono">
                      {duration(job.created_at, job.updated_at)}
                    </Td>
                    <Td className="text-xs text-muted">
                      {new Date(job.created_at).toLocaleString(undefined, {
                        month: 'short',
                        day: 'numeric',
                        hour: '2-digit',
                        minute: '2-digit',
                      })}
                    </Td>
                  </Tr>
                ))}
              </Tbody>
            </Table>
          </div>
          </>
        )}
      </div>
    </div>
  )
}
