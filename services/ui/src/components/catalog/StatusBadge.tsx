import { Badge } from '../ui'
import type { ResourceStatus } from '../../lib/api'

const STATUS: Record<ResourceStatus, { label: string; variant: 'success' | 'danger' | 'default' | 'muted' }> = {
  ok: { label: 'Reachable', variant: 'success' },
  error: { label: 'Error', variant: 'danger' },
  pending_secret: { label: 'Credential missing', variant: 'default' },
  unknown: { label: 'Unknown', variant: 'muted' },
}

export default function StatusBadge({ status, error }: { status: ResourceStatus; error?: string | null }) {
  const entry = STATUS[status] ?? STATUS.unknown
  return (
    <span title={error ?? undefined}>
      <Badge variant={entry.variant}>{entry.label}</Badge>
    </span>
  )
}
