import { useCallback, useEffect, useState, type FormEvent } from 'react'
import { Plus, RefreshCw, Trash2, Braces, ChevronDown, ChevronRight } from 'lucide-react'
import {
  api,
  type ApiContract,
  type ApiContractCreateRequest,
  type ApiContractType,
  type ApiEndpointDetail,
  type ApiEndpointSummary,
} from '../lib/api'
import { Badge, Button, Dialog, DialogFooter, Input, Select, Textarea, useConfirm, useToast } from '../components/ui'

const METHOD_COLOR: Record<string, string> = {
  GET: '#1a7a45',
  POST: '#2563eb',
  PUT: '#9a6108',
  PATCH: '#9a6108',
  DELETE: '#c0392b',
  QUERY: '#1a7a45',
  MUTATION: '#2563eb',
}

function MethodBadge({ method }: { method: string }) {
  return (
    <span
      style={{ color: METHOD_COLOR[method] ?? 'var(--muted)', minWidth: '4.5rem' }}
      className="inline-block font-mono text-xs font-semibold flex-shrink-0"
    >
      {method}
    </span>
  )
}

function statusVariant(status: ApiContract['status']) {
  if (status === 'ready') return 'success' as const
  if (status === 'error') return 'danger' as const
  return 'warning' as const
}

// ---------------------------------------------------------------------------
// Create dialog
// ---------------------------------------------------------------------------
function ContractDialog({
  open,
  onOpenChange,
  onSaved,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  onSaved: () => void
}) {
  const toast = useToast()
  const [form, setForm] = useState<ApiContractCreateRequest>({ name: '', type: 'openapi' })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (open) {
      setForm({ name: '', type: 'openapi' })
      setError(null)
    }
  }, [open])

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    if (!form.source_url && !form.raw_spec) {
      setError('Provide a URL or paste the spec content.')
      return
    }
    setSaving(true)
    setError(null)
    try {
      const res = await api.contracts.create(form)
      onOpenChange(false)
      onSaved()
      if (res.contract.status === 'error') {
        toast.error(`Contract created but ingestion failed: ${res.contract.error_message}`)
      } else {
        toast.success('Contract created')
      }
    } catch (err) {
      setError(String(err))
    } finally {
      setSaving(false)
    }
  }

  const isGraphql = form.type === 'graphql'
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="Add API contract"
      description="Ingest an OpenAPI/Swagger spec or a GraphQL schema via introspection."
      maxWidth="560px"
    >
      <form onSubmit={submit} className="space-y-3">
        {error && <p className="text-xs text-danger break-words">{error}</p>}
        <div className="grid grid-cols-2 gap-2">
          <Input
            label="Name"
            value={form.name}
            onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
            placeholder="e.g. billing-service"
            required
          />
          <Select
            label="Type"
            value={form.type}
            onValueChange={(v) => setForm((f) => ({ ...f, type: v as ApiContractType }))}
            options={[
              { value: 'openapi', label: 'OpenAPI / Swagger' },
              { value: 'graphql', label: 'GraphQL' },
            ]}
          />
        </div>
        <Input
          label={isGraphql ? 'GraphQL endpoint URL' : 'Spec URL'}
          value={form.source_url ?? ''}
          onChange={(e) => setForm((f) => ({ ...f, source_url: e.target.value || undefined }))}
          placeholder={isGraphql ? 'https://api.example.com/graphql' : 'https://api.example.com/openapi.json'}
          hint={isGraphql ? 'The standard introspection query is executed against this endpoint.' : 'Fetched now and on every refresh.'}
        />
        <Textarea
          label={isGraphql ? 'Or paste introspection result JSON' : 'Or paste the spec (JSON / YAML)'}
          value={form.raw_spec ?? ''}
          onChange={(e) => setForm((f) => ({ ...f, raw_spec: e.target.value || undefined }))}
          rows={5}
          className="font-mono text-xs"
        />
        <Input
          label="Description"
          value={form.description ?? ''}
          onChange={(e) => setForm((f) => ({ ...f, description: e.target.value || undefined }))}
          placeholder="What this service does (shown to agents)"
        />
        <DialogFooter>
          <Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" loading={saving}>
            Ingest
          </Button>
        </DialogFooter>
      </form>
    </Dialog>
  )
}

// ---------------------------------------------------------------------------
// Endpoint browser for one contract
// ---------------------------------------------------------------------------
function EndpointRow({ contract, endpoint }: { contract: ApiContract; endpoint: ApiEndpointSummary }) {
  const [open, setOpen] = useState(false)
  const [detail, setDetail] = useState<ApiEndpointDetail | null>(null)
  const [error, setError] = useState<string | null>(null)

  const toggle = async () => {
    const next = !open
    setOpen(next)
    if (next && !detail) {
      try {
        setDetail(await api.contracts.endpoint(contract.id, endpoint.method, endpoint.path))
      } catch (e) {
        setError(String(e))
      }
    }
  }

  return (
    <div style={{ borderBottom: '1px solid var(--border)' }} className="last:border-b-0">
      <button
        onClick={toggle}
        className="w-full text-left px-3 py-2 flex items-center gap-2 hover:bg-surface transition-colors"
      >
        {open ? (
          <ChevronDown className="w-3.5 h-3.5 text-muted flex-shrink-0" />
        ) : (
          <ChevronRight className="w-3.5 h-3.5 text-muted flex-shrink-0" />
        )}
        <MethodBadge method={endpoint.method} />
        <code className="font-mono text-xs truncate">{endpoint.path}</code>
        {endpoint.deprecated && <Badge variant="warning">deprecated</Badge>}
        <span className="text-xs text-muted truncate ml-auto">{endpoint.summary}</span>
      </button>
      {open && (
        <div className="px-9 pb-3 space-y-2">
          {error && <p className="text-xs text-danger">{error}</p>}
          {!detail && !error && <p className="text-xs text-muted">Loading…</p>}
          {detail && (
            <>
              {detail.description && <p className="text-xs text-muted">{detail.description}</p>}
              {detail.tags.length > 0 && (
                <div className="flex gap-1 flex-wrap">
                  {detail.tags.map((t) => (
                    <Badge key={t} variant="muted">{t}</Badge>
                  ))}
                </div>
              )}
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-2">
                <div>
                  <p className="text-xs font-medium mb-1">Request</p>
                  <pre
                    style={{ border: '1px solid var(--border)', background: 'var(--code-bg)' }}
                    className="text-xs font-mono p-2 overflow-x-auto max-h-64 overflow-y-auto whitespace-pre-wrap break-words"
                  >
                    {JSON.stringify(detail.request_schema, null, 2)}
                  </pre>
                </div>
                <div>
                  <p className="text-xs font-medium mb-1">Response</p>
                  <pre
                    style={{ border: '1px solid var(--border)', background: 'var(--code-bg)' }}
                    className="text-xs font-mono p-2 overflow-x-auto max-h-64 overflow-y-auto whitespace-pre-wrap break-words"
                  >
                    {JSON.stringify(detail.response_schema, null, 2)}
                  </pre>
                </div>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}

function ContractCard({
  contract,
  onChanged,
}: {
  contract: ApiContract
  onChanged: () => void
}) {
  const toast = useToast()
  const confirm = useConfirm()
  const [expanded, setExpanded] = useState(false)
  const [endpoints, setEndpoints] = useState<ApiEndpointSummary[]>([])
  const [filter, setFilter] = useState('')
  const [refreshing, setRefreshing] = useState(false)

  const loadEndpoints = useCallback(async () => {
    const data = await api.contracts.endpoints(contract.id, filter ? { search: filter } : undefined)
    setEndpoints(data.endpoints)
  }, [contract.id, filter])

  useEffect(() => {
    if (expanded) loadEndpoints().catch(() => setEndpoints([]))
  }, [expanded, loadEndpoints])

  const refresh = async () => {
    setRefreshing(true)
    try {
      await api.contracts.refresh(contract.id)
      toast.success('Contract refreshed')
      onChanged()
      if (expanded) await loadEndpoints()
    } catch (e) {
      toast.error(String(e))
    } finally {
      setRefreshing(false)
    }
  }

  const remove = async () => {
    const ok = await confirm({
      title: 'Delete contract',
      message: `Delete contract '${contract.name}'?`,
      confirmLabel: 'Delete',
      danger: true,
      onConfirm: () => api.contracts.delete(contract.id),
    })
    if (!ok) return
    toast.success('Contract deleted')
    onChanged()
  }

  return (
    <div style={{ border: '1px solid var(--border)' }}>
      <div className="px-4 py-3 flex items-center gap-3 flex-wrap">
        <button
          onClick={() => setExpanded((v) => !v)}
          className="flex items-center gap-2 text-left min-w-0"
        >
          {expanded ? (
            <ChevronDown className="w-4 h-4 text-muted flex-shrink-0" />
          ) : (
            <ChevronRight className="w-4 h-4 text-muted flex-shrink-0" />
          )}
          <span className="font-medium text-sm">{contract.name}</span>
          <Badge variant="muted">{contract.type}</Badge>
          <Badge variant={statusVariant(contract.status)}>{contract.status}</Badge>
        </button>
        <span className="text-xs text-muted">
          {contract.title ? `${contract.title} ` : ''}
          {contract.version ? `v${contract.version} · ` : ''}
          {contract.endpoint_count} operations
        </span>
        <div className="flex items-center gap-1 ml-auto">
          <Button size="sm" variant="ghost" loading={refreshing} onClick={refresh} title="Re-fetch and re-parse">
            <RefreshCw className="w-3.5 h-3.5" />
          </Button>
          <Button size="sm" variant="ghost" onClick={remove} title="Delete">
            <Trash2 className="w-3.5 h-3.5" />
          </Button>
        </div>
      </div>
      {contract.status === 'error' && contract.error_message && (
        <p className="text-xs text-danger px-4 pb-3 break-words">{contract.error_message}</p>
      )}
      {expanded && (
        <div style={{ borderTop: '1px solid var(--border)' }}>
          <div className="p-3">
            <Input
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && loadEndpoints()}
              placeholder="Filter by path, summary, tag… (Enter to apply)"
            />
          </div>
          <div className="max-h-[28rem] overflow-y-auto scrollbar-thin">
            {endpoints.map((ep) => (
              <EndpointRow key={ep.id} contract={contract} endpoint={ep} />
            ))}
            {endpoints.length === 0 && (
              <p className="text-xs text-muted px-4 pb-3">No operations match.</p>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------
export default function ApiContracts() {
  const [contracts, setContracts] = useState<ApiContract[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [dialogOpen, setDialogOpen] = useState(false)

  const load = useCallback(async () => {
    try {
      const data = await api.contracts.list()
      setContracts(data.contracts)
      setError(null)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  return (
    <div className="p-4 sm:p-8">
      <div className="page-wide">
        <div className="flex items-start justify-between gap-4 mb-6">
          <div>
            <h1>API Contracts</h1>
            <p className="text-muted text-sm">
              OpenAPI specs and GraphQL schemas exposed to agents via the api_* MCP tools.
            </p>
          </div>
          <Button variant="primary" onClick={() => setDialogOpen(true)}>
            <Plus className="w-3.5 h-3.5" /> Add contract
          </Button>
        </div>

        {error && (
          <div
            style={{ border: '1px solid var(--danger)', color: 'var(--danger)' }}
            className="text-sm p-3 mb-4 bg-[#fef2f2] break-words"
          >
            {error}
          </div>
        )}

        {loading ? (
          <p className="text-muted text-sm">Loading...</p>
        ) : contracts.length === 0 ? (
          <div
            style={{ border: '1px dashed var(--border)' }}
            className="p-8 text-center text-sm text-muted"
          >
            <Braces className="w-6 h-6 mx-auto mb-2 opacity-50" />
            No API contracts yet. Ingest an OpenAPI spec or a GraphQL schema so agents
            know which endpoints exist and with what payloads.
          </div>
        ) : (
          <div className="space-y-3">
            {contracts.map((c) => (
              <ContractCard key={c.id} contract={c} onChanged={load} />
            ))}
          </div>
        )}

        <ContractDialog open={dialogOpen} onOpenChange={setDialogOpen} onSaved={load} />
      </div>
    </div>
  )
}
