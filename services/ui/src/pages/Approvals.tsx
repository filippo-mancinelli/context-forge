import { useCallback, useEffect, useRef, useState } from 'react'
import { ShieldCheck, Database, FileText } from 'lucide-react'
import { api, type WriteRequest, type WriteRequestStatus } from '../lib/api'
import { formatAge, isExpired } from '../lib/approvals'
import { useAppStore } from '../store'
import {
  Badge, Banner, Button, Card, Dialog, DialogFooter, Select,
  Table, Tbody, Td, Textarea, Th, Thead, Tr, useToast,
} from '../components/ui'

const STATUS_OPTIONS = [
  { value: 'pending', label: 'Pending' },
  { value: 'approved', label: 'Approved' },
  { value: 'executed', label: 'Executed' },
  { value: 'failed', label: 'Failed' },
  { value: 'rejected', label: 'Rejected' },
  { value: 'expired', label: 'Expired' },
  { value: 'all', label: 'All' },
]

function statusBadge(status: WriteRequestStatus) {
  if (status === 'pending') return <Badge variant="warning">Pending</Badge>
  if (status === 'executed') return <Badge variant="success">Executed</Badge>
  if (status === 'failed') return <Badge variant="danger">Failed</Badge>
  if (status === 'rejected') return <Badge variant="danger">Rejected</Badge>
  if (status === 'expired') return <Badge variant="muted">Expired</Badge>
  return <Badge variant="accent">Approved</Badge>
}

function KindIcon({ kind }: { kind: WriteRequest['kind'] }) {
  return kind === 'db_execute'
    ? <Database className="w-3.5 h-3.5 text-muted" />
    : <FileText className="w-3.5 h-3.5 text-muted" />
}

function summary(req: WriteRequest): string {
  if (req.kind === 'db_execute') return String(req.payload.sql ?? '')
  const preview = req.preview as { is_new?: boolean; content_bytes?: number }
  return `${preview.is_new ? 'New file' : 'Modified file'} — ${preview.content_bytes ?? 0} bytes`
}

function DetailDrawer({
  request, onClose, onDecided,
}: {
  request: WriteRequest
  onClose: () => void
  onDecided: () => void
}) {
  const toast = useToast()
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState<'approve' | 'reject' | null>(null)
  const preview = request.preview as Record<string, unknown>
  const expired = isExpired(request.expires_at)

  const decide = async (action: 'approve' | 'reject') => {
    setBusy(action)
    try {
      const res = action === 'approve'
        ? await api.writeRequests.approve(request.id, note)
        : await api.writeRequests.reject(request.id, note)
      toast.success(`Request ${res.request.status}`)
      onDecided()
      onClose()
    } catch (e) {
      toast.error(String(e))
    } finally {
      setBusy(null)
    }
  }

  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={`${request.kind === 'db_execute' ? 'SQL write' : 'File write'} — ${request.target}`}
      description={`Proposed by ${request.requested_by} (${request.requested_by_kind}) ${formatAge(request.created_at)}`}
      maxWidth="720px"
    >
      <div className="space-y-4">
        {request.reason && (
          <div>
            <div className="text-xs font-semibold uppercase tracking-wide text-muted">Reason</div>
            <p className="text-sm">{request.reason}</p>
          </div>
        )}

        {request.kind === 'db_execute' ? (
          <div className="space-y-2">
            <div className="text-xs font-semibold uppercase tracking-wide text-muted">Statement</div>
            <Card className="p-3 overflow-x-auto">
              <pre className="text-xs font-mono whitespace-pre-wrap">{String(request.payload.sql ?? '')}</pre>
            </Card>
            <div className="text-sm text-muted">
              Estimated rows: {preview.plan_rows === null || preview.plan_rows === undefined
                ? 'unknown'
                : String(preview.plan_rows)}
            </div>
            {typeof preview.explain_error === 'string' && (
              <Banner variant="warning">EXPLAIN unavailable: {preview.explain_error}</Banner>
            )}
            {typeof preview.plan_text === 'string' && preview.plan_text !== '' && (
              <Card className="p-3 overflow-x-auto">
                <pre className="text-xs font-mono whitespace-pre-wrap">{preview.plan_text}</pre>
              </Card>
            )}
          </div>
        ) : (
          <div className="space-y-2">
            <div className="text-xs font-semibold uppercase tracking-wide text-muted">File</div>
            <Table>
              <Tbody>
                <Tr><Td className="text-muted">Path</Td><Td className="font-mono">{String(preview.path ?? '')}</Td></Tr>
                <Tr><Td className="text-muted">Change</Td><Td>{preview.is_new ? 'New file' : 'Overwrite'}</Td></Tr>
                <Tr><Td className="text-muted">New size</Td><Td>{String(preview.content_bytes ?? 0)} bytes</Td></Tr>
                <Tr><Td className="text-muted">New sha256</Td><Td className="font-mono text-xs break-all">{String(preview.content_sha256 ?? '')}</Td></Tr>
                <Tr><Td className="text-muted">Current size</Td><Td>{preview.existing_size === null || preview.existing_size === undefined ? '—' : `${String(preview.existing_size)} bytes`}</Td></Tr>
                <Tr><Td className="text-muted">Current sha256</Td><Td className="font-mono text-xs break-all">{String(preview.existing_sha256 ?? '—')}</Td></Tr>
                {typeof preview.read_error === 'string' && (
                  <Tr><Td className="text-muted">Read error</Td><Td className="text-danger">{preview.read_error}</Td></Tr>
                )}
              </Tbody>
            </Table>
            <Card className="p-3 max-h-64 overflow-auto">
              <pre className="text-xs font-mono whitespace-pre-wrap">{String(request.payload.content ?? '')}</pre>
            </Card>
          </div>
        )}

        {request.status === 'executed' && request.result && (
          <Banner variant="success">
            Executed: {JSON.stringify(request.result)}
          </Banner>
        )}
        {request.status === 'failed' && request.error && (
          <Banner variant="danger">Failed: {request.error}</Banner>
        )}
        {request.decision_note && (
          <div className="text-sm text-muted">Decision note: {request.decision_note}</div>
        )}

        {request.status === 'pending' && !expired && (
          <Textarea
            label="Note (optional)"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            rows={2}
          />
        )}
        {request.status === 'pending' && expired && (
          <Banner variant="warning">This request has expired and can no longer be approved.</Banner>
        )}
      </div>

      <DialogFooter>
        <Button variant="ghost" onClick={onClose}>Close</Button>
        {request.status === 'pending' && !expired && (
          <>
            <Button
              variant="danger"
              loading={busy === 'reject'}
              disabled={busy !== null}
              onClick={() => decide('reject')}
            >
              Reject
            </Button>
            <Button
              variant="primary"
              loading={busy === 'approve'}
              disabled={busy !== null}
              onClick={() => decide('approve')}
            >
              Approve and run
            </Button>
          </>
        )}
      </DialogFooter>
    </Dialog>
  )
}

export default function Approvals() {
  const toast = useToast()
  const role = useAppStore((s) => s.organizations.find((o) => o.id === s.activeOrgId)?.role)
  const activeOrgId = useAppStore((s) => s.activeOrgId)
  const loadPendingApprovals = useAppStore((s) => s.loadPendingApprovals)
  const canApprove = role === 'admin' || role === 'owner'
  const [status, setStatus] = useState('pending')
  const [requests, setRequests] = useState<WriteRequest[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState<WriteRequest | null>(null)
  // Bumped on every fetch so a slower, stale response (e.g. an earlier filter
  // or org change) landing after a newer one can be discarded.
  const requestSeq = useRef(0)

  const load = useCallback(async () => {
    if (!canApprove) {
      setLoading(false)
      return
    }
    const seq = ++requestSeq.current
    setLoading(true)
    try {
      const res = await api.writeRequests.list({
        status: status === 'all' ? undefined : status,
        limit: 100,
      })
      if (requestSeq.current !== seq) return
      setRequests(res.requests)
      setTotal(res.total)
    } catch (e) {
      if (requestSeq.current === seq) toast.error(String(e))
    } finally {
      if (requestSeq.current === seq) setLoading(false)
    }
  }, [canApprove, status, toast])

  useEffect(() => {
    void load()
    // activeOrgId isn't read directly here, but a switch must still refetch —
    // canApprove/status can stay the same across orgs while the data differs.
  }, [load, activeOrgId])

  const afterDecision = async () => {
    await load()
    await loadPendingApprovals()
  }

  if (!canApprove) {
    return (
      <div className="p-4 sm:p-8">
        <div className="page-wide">
          <Banner variant="info">
            Only organization admins and owners can review write requests.
          </Banner>
        </div>
      </div>
    )
  }

  return (
    <div className="p-4 sm:p-8">
      <div className="page-wide space-y-4">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1>Approvals</h1>
            <p className="text-sm text-muted">
              SQL statements and file writes proposed by agents that lack the write
              permission. Approving runs the change under your identity.
            </p>
          </div>
          <div className="page-header-actions">
            <Select
              value={status}
              onValueChange={setStatus}
              options={STATUS_OPTIONS}
              className="w-40"
            />
          </div>
        </div>

        {loading ? (
          <p className="text-sm text-muted">Loading…</p>
        ) : requests.length === 0 ? (
          <div className="rounded border border-dashed border-border p-8 text-center">
            <ShieldCheck className="w-6 h-6 mx-auto text-muted" />
            <p className="mt-2 text-sm text-muted">Nothing to review here.</p>
          </div>
        ) : (
          <>
            <Table>
              <Thead>
                <Tr>
                  <Th>Target</Th>
                  <Th>Change</Th>
                  <Th>Requested by</Th>
                  <Th>Age</Th>
                  <Th>Status</Th>
                  <Th className="w-24" />
                </Tr>
              </Thead>
              <Tbody>
                {requests.map((r) => (
                  <Tr key={r.id}>
                    <Td>
                      <span className="inline-flex items-center gap-1.5 font-medium">
                        <KindIcon kind={r.kind} /> {r.target}
                      </span>
                    </Td>
                    <Td className="font-mono text-xs max-w-md truncate">{summary(r)}</Td>
                    <Td className="text-sm">{r.requested_by}</Td>
                    <Td className="text-sm text-muted">
                      {formatAge(r.created_at)}
                      {r.status === 'pending' && isExpired(r.expires_at) && (
                        <Badge variant="muted" className="ml-2">expired</Badge>
                      )}
                    </Td>
                    <Td>{statusBadge(r.status)}</Td>
                    <Td>
                      <div className="flex justify-end">
                        <Button size="sm" variant="secondary" onClick={() => setSelected(r)}>
                          Review
                        </Button>
                      </div>
                    </Td>
                  </Tr>
                ))}
              </Tbody>
            </Table>
            <p className="text-xs text-muted">
              Showing {requests.length} of {total} request{total === 1 ? '' : 's'}.
            </p>
          </>
        )}

        {selected && (
          <DetailDrawer
            request={selected}
            onClose={() => setSelected(null)}
            onDecided={afterDecision}
          />
        )}
      </div>
    </div>
  )
}
