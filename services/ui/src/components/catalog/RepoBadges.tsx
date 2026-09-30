import { ExternalLink, GitlabIcon, Github, HardDrive } from 'lucide-react'
import type { Repo } from '../../lib/api'
import { Badge } from '../ui'

const STATUS_VARIANT: Record<Repo['status'], 'success' | 'accent' | 'warning' | 'danger'> = {
  indexed: 'success',
  indexing: 'accent',
  pending: 'warning',
  error: 'danger',
}

export function RepoStatusBadge({ repo }: { repo: Repo }) {
  return (
    <>
      <Badge variant={STATUS_VARIANT[repo.status] ?? 'warning'}>{repo.status}</Badge>
      {repo.error_message && (
        <p className="text-xs text-danger mt-1 max-w-xs break-words" title={repo.error_message}>
          {repo.error_message}
        </p>
      )}
    </>
  )
}

export function RepoTypeIcon({ type }: { type: Repo['type'] }) {
  if (type === 'github') return <Github className="w-3.5 h-3.5 text-muted" />
  if (type === 'gitlab') return <GitlabIcon className="w-3.5 h-3.5 text-warning" />
  return <HardDrive className="w-3.5 h-3.5 text-muted" />
}

// Indirizzo completo del repository: un URL remoto si apre sul provider, un percorso locale resta testo.
export function RepoSource({ repo }: { repo: Repo }) {
  const location = repo.url || repo.path
  if (!location) return <span>—</span>
  if (/^https?:\/\//i.test(location)) {
    return (
      <a
        href={location}
        target="_blank"
        rel="noreferrer"
        className="inline-flex items-start gap-1 font-mono break-all hover:text-accent hover:underline"
      >
        <span>{location}</span>
        <ExternalLink className="w-3 h-3 mt-0.5 flex-shrink-0" />
      </a>
    )
  }
  return <span className="font-mono break-all">{location}</span>
}

export function formatIndexedAt(iso?: string) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}
