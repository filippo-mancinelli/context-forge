import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft, Play, Save, Table2, KeyRound, Link2 } from 'lucide-react'
import {
  api,
  type DbConnection,
  type DbQueryLogEntry,
  type DbQueryResult,
  type DbSchemaOverview,
  type DbTableDetail,
} from '../lib/api'
import {
  Badge,
  Button,
  Input,
  Select,
  Table,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
  Tbody,
  Td,
  Textarea,
  Th,
  Thead,
  Tr,
  useToast,
} from '../components/ui'

function formatRows(n?: number | null): string {
  if (n === null || n === undefined) return '—'
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`
  return String(n)
}

// ---------------------------------------------------------------------------
// Schema tab: table list + table detail with editable data dictionary
// ---------------------------------------------------------------------------
function TableDetailPanel({
  connectionId,
  table,
  schema,
  onSaved,
}: {
  connectionId: number
  table: string
  schema?: string
  onSaved: () => void
}) {
  const toast = useToast()
  const [detail, setDetail] = useState<DbTableDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [editing, setEditing] = useState(false)
  const [tableDesc, setTableDesc] = useState('')
  const [colDescs, setColDescs] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)

  const load = useCallback(async () => {
    setDetail(null)
    setError(null)
    setEditing(false)
    try {
      const d = await api.datasources.table(connectionId, table, { schema })
      setDetail(d)
      setTableDesc(d.description ?? '')
      setColDescs(Object.fromEntries(d.columns.map((c) => [c.name, c.description ?? ''])))
    } catch (e) {
      setError(String(e))
    }
  }, [connectionId, table, schema])

  useEffect(() => {
    load()
  }, [load])

  const save = async () => {
    if (!detail) return
    setSaving(true)
    try {
      // schema_name '' means "any schema" so single-schema DBs stay simple.
      const annotations = [
        { schema_name: '', table_name: table, column_name: '', description: tableDesc },
        ...detail.columns.map((c) => ({
          schema_name: '',
          table_name: table,
          column_name: c.name,
          description: colDescs[c.name] ?? '',
        })),
      ]
      await api.datasources.saveAnnotations(connectionId, annotations)
      toast.success('Descriptions saved')
      setEditing(false)
      await load()
      onSaved()
    } catch (e) {
      setError(String(e))
    } finally {
      setSaving(false)
    }
  }

  if (error) return <p className="text-sm text-danger break-words">{error}</p>
  if (!detail) return <p className="text-sm text-muted">Loading table…</p>

  const pk = new Set(detail.primary_key)
  const fkByColumn = new Map<string, string>()
  for (const fk of detail.foreign_keys) {
    fk.columns.forEach((col, i) => {
      fkByColumn.set(col, `${fk.referred_table}.${fk.referred_columns[i] ?? ''}`)
    })
  }

  return (
    <div className="space-y-4 min-w-0">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-base font-semibold flex items-center gap-2">
            <Table2 className="w-4 h-4 text-muted" /> {detail.table}
            <span className="text-xs text-muted font-normal">
              ~{formatRows(detail.estimated_rows)} rows
            </span>
          </h2>
          {detail.comment && <p className="text-xs text-muted mt-0.5">DB comment: {detail.comment}</p>}
        </div>
        {editing ? (
          <div className="flex gap-1 flex-shrink-0">
            <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>
              Cancel
            </Button>
            <Button size="sm" variant="primary" loading={saving} onClick={save}>
              <Save className="w-3.5 h-3.5" /> Save
            </Button>
          </div>
        ) : (
          <Button size="sm" variant="secondary" onClick={() => setEditing(true)} className="flex-shrink-0">
            Edit descriptions
          </Button>
        )}
      </div>

      {editing ? (
        <Textarea
          label="Table description"
          value={tableDesc}
          onChange={(e) => setTableDesc(e.target.value)}
          placeholder="What this table represents, conventions, gotchas… (injected into agent context)"
          rows={2}
        />
      ) : (
        detail.description && (
          <p
            style={{ borderLeft: '2px solid var(--accent)' }}
            className="text-sm text-text pl-3 py-1 bg-surface"
          >
            {detail.description}
          </p>
        )
      )}

      <div style={{ border: '1px solid var(--border)' }} className="overflow-x-auto">
        <Table>
          <Thead>
            <Tr>
              <Th>Column</Th>
              <Th>Type</Th>
              <Th>Attrs</Th>
              <Th>Description</Th>
            </Tr>
          </Thead>
          <Tbody>
            {detail.columns.map((col) => (
              <Tr key={col.name}>
                <Td>
                  <span className="font-mono text-xs">{col.name}</span>
                  {pk.has(col.name) && (
                    <KeyRound className="w-3 h-3 inline-block ml-1 text-warning" aria-label="primary key" />
                  )}
                </Td>
                <Td className="font-mono text-xs text-muted">{col.type}</Td>
                <Td className="text-xs text-muted">
                  {[
                    !col.nullable ? 'NOT NULL' : null,
                    col.autoincrement ? 'auto' : null,
                    col.default ? `default ${col.default}` : null,
                  ]
                    .filter(Boolean)
                    .join(' · ')}
                  {fkByColumn.has(col.name) && (
                    <span className="inline-flex items-center gap-0.5 ml-1 text-accent">
                      <Link2 className="w-3 h-3" /> {fkByColumn.get(col.name)}
                    </span>
                  )}
                </Td>
                <Td className="text-xs">
                  {editing ? (
                    <Input
                      value={colDescs[col.name] ?? ''}
                      onChange={(e) => setColDescs((m) => ({ ...m, [col.name]: e.target.value }))}
                      placeholder="e.g. status: O=Offline, A=Online…"
                    />
                  ) : (
                    <span className={col.description ? 'text-text' : 'text-muted'}>
                      {col.description ?? col.comment ?? ''}
                    </span>
                  )}
                </Td>
              </Tr>
            ))}
          </Tbody>
        </Table>
      </div>

      {detail.indexes.length > 0 && (
        <div className="text-xs text-muted">
          <span className="font-medium text-text">Indexes:</span>{' '}
          {detail.indexes
            .map((i) => `${i.name ?? '(unnamed)'} (${i.columns.join(', ')})${i.unique ? ' unique' : ''}`)
            .join(' · ')}
        </div>
      )}
    </div>
  )
}

function SchemaTab({ connectionId }: { connectionId: number }) {
  const [overview, setOverview] = useState<DbSchemaOverview | null>(null)
  const [schema, setSchema] = useState<string | undefined>(undefined)
  const [selectedTable, setSelectedTable] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const data = await api.datasources.schema(connectionId, schema)
      setOverview(data)
      setError(null)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [connectionId, schema])

  useEffect(() => {
    load()
  }, [load])

  if (loading && !overview) return <p className="text-sm text-muted">Introspecting schema…</p>
  if (error) return <p className="text-sm text-danger break-words">{error}</p>
  if (!overview) return null

  return (
    <div className="space-y-4">
      {overview.schemas.length > 1 && (
        <div className="max-w-xs">
          <Select
            label="Schema"
            value={overview.schema ?? ''}
            onValueChange={(v) => {
              setSelectedTable(null)
              setSchema(v)
            }}
            options={overview.schemas.map((s) => ({ value: s, label: s }))}
          />
        </div>
      )}
      <div className="flex flex-col lg:flex-row gap-4">
        <div
          style={{ border: '1px solid var(--border)' }}
          className="lg:w-72 flex-shrink-0 max-h-[32rem] overflow-y-auto"
        >
          {overview.tables.map((t) => (
            <button
              key={t.name}
              onClick={() => setSelectedTable(t.name)}
              className={[
                'w-full text-left px-3 py-2 text-sm border-b border-border transition-colors',
                selectedTable === t.name ? 'bg-surface text-accent font-medium' : 'hover:bg-surface',
              ].join(' ')}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="font-mono text-xs truncate">{t.name}</span>
                <span className="text-xs text-muted flex-shrink-0">{formatRows(t.estimated_rows)}</span>
              </div>
              {(t.description || t.comment) && (
                <p className="text-xs text-muted truncate mt-0.5">{t.description ?? t.comment}</p>
              )}
            </button>
          ))}
          {overview.tables.length === 0 && (
            <p className="text-xs text-muted p-3">No tables in this schema.</p>
          )}
          {overview.views.length > 0 && (
            <p className="text-xs text-muted px-3 py-2">
              Views: {overview.views.join(', ')}
            </p>
          )}
        </div>
        <div className="flex-1 min-w-0">
          {selectedTable ? (
            <TableDetailPanel
              connectionId={connectionId}
              table={selectedTable}
              schema={overview.schema ?? undefined}
              onSaved={load}
            />
          ) : (
            <p className="text-sm text-muted">
              {overview.tables.length} tables in <code className="font-mono">{overview.schema}</code> —
              select one to inspect columns, keys, and edit its data-dictionary descriptions.
            </p>
          )}
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// SQL console tab
// ---------------------------------------------------------------------------
function ConsoleTab({ connectionId }: { connectionId: number }) {
  const [sql, setSql] = useState('')
  const [result, setResult] = useState<DbQueryResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [running, setRunning] = useState(false)

  const run = async () => {
    if (!sql.trim()) return
    setRunning(true)
    setError(null)
    try {
      setResult(await api.datasources.query(connectionId, sql))
    } catch (e) {
      setResult(null)
      setError(String(e))
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="space-y-3">
      <Textarea
        label="Read-only SQL (SELECT / SHOW / EXPLAIN — a LIMIT is enforced automatically)"
        value={sql}
        onChange={(e) => setSql(e.target.value)}
        onKeyDown={(e) => {
          if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') run()
        }}
        placeholder="SELECT * FROM users"
        rows={4}
        className="font-mono text-xs"
      />
      <div className="flex items-center gap-3">
        <Button variant="primary" loading={running} onClick={run}>
          <Play className="w-3.5 h-3.5" /> Run
        </Button>
        {result && (
          <span className="text-xs text-muted">
            {result.row_count} rows{result.truncated ? ' (truncated)' : ''} · {result.duration_ms}ms
          </span>
        )}
      </div>
      {error && <p className="text-sm text-danger break-words">{error}</p>}
      {result && result.columns.length > 0 && (
        <div style={{ border: '1px solid var(--border)' }} className="overflow-x-auto max-h-[28rem] overflow-y-auto">
          <Table>
            <Thead>
              <Tr>
                {result.columns.map((c) => (
                  <Th key={c}>{c}</Th>
                ))}
              </Tr>
            </Thead>
            <Tbody>
              {result.rows.map((row, i) => (
                <Tr key={i}>
                  {result.columns.map((c) => (
                    <Td key={c} className="font-mono text-xs whitespace-nowrap max-w-xs truncate">
                      {row[c] === null || row[c] === undefined ? (
                        <span className="text-muted">NULL</span>
                      ) : (
                        String(row[c])
                      )}
                    </Td>
                  ))}
                </Tr>
              ))}
            </Tbody>
          </Table>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Query log tab
// ---------------------------------------------------------------------------
function LogTab({ connectionId }: { connectionId: number }) {
  const [log, setLog] = useState<DbQueryLogEntry[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api.datasources
      .log(connectionId, 100)
      .then((d) => setLog(d.log))
      .catch(() => setLog([]))
      .finally(() => setLoading(false))
  }, [connectionId])

  if (loading) return <p className="text-sm text-muted">Loading…</p>
  if (log.length === 0) return <p className="text-sm text-muted">No queries executed yet.</p>

  return (
    <div style={{ border: '1px solid var(--border)' }} className="overflow-x-auto">
      <Table>
        <Thead>
          <Tr>
            <Th>When</Th>
            <Th>Source</Th>
            <Th>SQL</Th>
            <Th>Result</Th>
          </Tr>
        </Thead>
        <Tbody>
          {log.map((entry) => (
            <Tr key={entry.id}>
              <Td className="text-xs text-muted whitespace-nowrap">
                {entry.created_at
                  ? new Date(entry.created_at).toLocaleString(undefined, {
                      month: 'short',
                      day: 'numeric',
                      hour: '2-digit',
                      minute: '2-digit',
                    })
                  : '—'}
              </Td>
              <Td>
                <Badge>{entry.source}</Badge>
              </Td>
              <Td>
                <code className="font-mono text-xs break-all">{entry.sql_text}</code>
              </Td>
              <Td className="text-xs whitespace-nowrap">
                {entry.success ? (
                  <span className="text-muted">
                    {entry.rows_returned} rows · {entry.duration_ms}ms
                  </span>
                ) : (
                  <span className="text-danger" title={entry.error_message}>
                    error
                  </span>
                )}
              </Td>
            </Tr>
          ))}
        </Tbody>
      </Table>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------
export default function DataSourceDetail() {
  const { connectionId } = useParams<{ connectionId: string }>()
  const id = useMemo(() => Number(connectionId), [connectionId])
  const [connection, setConnection] = useState<DbConnection | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.datasources
      .list()
      .then((d) => {
        const found = d.connections.find((c) => c.id === id) ?? null
        setConnection(found)
        if (!found) setError('Connection not found')
      })
      .catch((e) => setError(String(e)))
  }, [id])

  return (
    <div className="p-4 sm:p-8">
      <div className="page-wide">
        <Link to="/datasources" className="inline-flex items-center gap-1 text-xs text-muted hover:text-text mb-3">
          <ArrowLeft className="w-3.5 h-3.5" /> Data Sources
        </Link>
        {error && <p className="text-sm text-danger">{error}</p>}
        {connection && (
          <>
            <div className="mb-6">
              <h1 className="flex items-center gap-2">
                {connection.name}
                <Badge variant={connection.status === 'ok' ? 'success' : connection.status === 'error' ? 'danger' : 'default'}>
                  {connection.status}
                </Badge>
              </h1>
              <p className="text-muted text-sm font-mono">
                {connection.engine} ·{' '}
                {connection.engine === 'sqlite'
                  ? connection.database_name
                  : `${connection.host ?? '?'}${connection.port ? `:${connection.port}` : ''}/${connection.database_name ?? ''}`}
              </p>
              {connection.description && <p className="text-sm text-muted mt-1">{connection.description}</p>}
            </div>
            <Tabs defaultValue="schema">
              <TabsList>
                <TabsTrigger value="schema">Schema</TabsTrigger>
                <TabsTrigger value="console">SQL Console</TabsTrigger>
                <TabsTrigger value="log">Query Log</TabsTrigger>
              </TabsList>
              <TabsContent value="schema">
                <SchemaTab connectionId={id} />
              </TabsContent>
              <TabsContent value="console">
                <ConsoleTab connectionId={id} />
              </TabsContent>
              <TabsContent value="log">
                <LogTab connectionId={id} />
              </TabsContent>
            </Tabs>
          </>
        )}
      </div>
    </div>
  )
}
