import { useEffect, useId, useMemo, useRef, useState, type FormEvent } from 'react'
import { Check, RefreshCw } from 'lucide-react'
import {
  api,
  type DbAvailableScopes,
  type DbConnection,
  type DbProjectScopeRequest,
  type DbScopeOption,
} from '../../lib/api'
import { proposeAlias, scopeLabel } from '../../lib/dbScopes'
import { actionFailedSentence, errorDetail } from '../../lib/messages'
import { hasErrors, requiredError, type FieldErrors } from '../../lib/validation'
import { Badge, Banner, Button, Dialog, DialogFooter, Input, useToast } from '../ui'

type ScopeField = 'database' | 'alias'

interface ScopeDialogProps {
  open: boolean
  projectId: number
  /** Catalog connections to choose from when adding. */
  catalog: DbConnection[]
  /** Links already in the project: proposed alias and scopes in use. */
  projectScopes: DbConnection[]
  /** Link to change; null = add. */
  editing: DbConnection | null
  allowRestricted: boolean
  onClose: () => void
  onSaved: () => void
}

function endpoint(c: DbConnection): string {
  if (c.engine === 'sqlite') return c.database_name ?? ''
  const address = `${c.host ?? '?'}${c.port ? `:${c.port}` : ''}`
  return c.ssh_machine_name ? `${address} via ${c.ssh_machine_name}` : address
}

function scopeKey(database?: string | null, schema?: string | null): string {
  return `${(database ?? '').trim()}|${(schema ?? '').trim()}`
}

// Adds in two steps (connection, then scope) or changes the scope of a link. A change asks
// for an explicit confirmation, because it moves the data the project's agents see.
export default function ScopeDialog({
  open, projectId, catalog, projectScopes, editing, allowRestricted, onClose, onSaved,
}: ScopeDialogProps) {
  const toast = useToast()
  const ids = useId()
  const [query, setQuery] = useState('')
  const [connection, setConnection] = useState<DbConnection | null>(null)
  const [available, setAvailable] = useState<DbAvailableScopes | null>(null)
  const [loadingScopes, setLoadingScopes] = useState(false)
  const [database, setDatabase] = useState('')
  const [schema, setSchema] = useState('')
  const [alias, setAlias] = useState('')
  const [aliasTouched, setAliasTouched] = useState(false)
  const [errors, setErrors] = useState<FieldErrors<ScopeField>>({})
  const [error, setError] = useState<string | null>(null)
  const [confirming, setConfirming] = useState(false)
  const [saving, setSaving] = useState(false)

  // Connection whose scopes are being read: a reply that arrives after switching to
  // another connection no longer concerns this choice.
  const listingFor = useRef<number | null>(null)

  const loadScopes = async (connectionId: number) => {
    listingFor.current = connectionId
    setLoadingScopes(true)
    try {
      const listed = await api.catalog.databases.scopes(connectionId, true)
      if (listingFor.current !== connectionId) return
      setAvailable(listed)
    } catch (e) {
      if (listingFor.current !== connectionId) return
      setAvailable({ scopes: [], checked_at: null, live: false, error: errorDetail(e) })
    } finally {
      if (listingFor.current === connectionId) setLoadingScopes(false)
    }
  }

  const backToCatalog = () => {
    listingFor.current = null
    setLoadingScopes(false)
    setConnection(null)
  }

  const start = (c: DbConnection, db: string, sch: string, currentAlias: string | null) => {
    listingFor.current = c.id
    setConnection(c)
    setDatabase(db)
    setSchema(sch)
    setAlias(currentAlias ?? '')
    setAliasTouched(currentAlias != null)
    setErrors({})
    setError(null)
    setConfirming(false)
    setAvailable(null)
    if (c.engine !== 'sqlite') void loadScopes(c.id)
  }

  useEffect(() => {
    if (!open) return
    setQuery('')
    if (editing) {
      // An inferred scope does not narrow the connection: it works on the database the
      // connection has today, which is what the row shows. Confirming it keeps that database.
      const currentDatabase = editing.scope_inferred
        ? editing.database_name ?? ''
        : editing.scope_database ?? ''
      start(editing, currentDatabase, editing.scope_schema ?? '', editing.alias ?? editing.name)
    } else {
      backToCatalog()
      setAvailable(null)
      setErrors({})
      setError(null)
      setConfirming(false)
    }
  }, [open, editing])

  const choose = (c: DbConnection) =>
    start(c, c.engine === 'sqlite' ? '' : c.database_name ?? '', c.engine === 'postgresql' ? 'public' : '', null)

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase()
    return q ? catalog.filter((c) => `${c.name} ${c.engine} ${endpoint(c)}`.toLowerCase().includes(q)) : catalog
  }, [catalog, query])

  const inUse = useMemo(
    () =>
      new Set(
        projectScopes
          .filter((s) => connection != null && s.id === connection.id && s.scope_id !== editing?.scope_id)
          .map((s) => scopeKey(s.scope_database, s.scope_schema)),
      ),
    [projectScopes, connection, editing],
  )

  const isSqlite = connection?.engine === 'sqlite'
  const isPostgres = connection?.engine === 'postgresql'
  const label = connection ? scopeLabel(connection.engine, database, schema) : ''
  const shownAlias = aliasTouched || !connection ? alias : proposeAlias(projectScopes, connection, label)
  const chosenKey = scopeKey(database, isPostgres ? schema.trim() || 'public' : null)

  const changeDatabase = (value: string) => {
    setDatabase(value)
    setConfirming(false)
    setErrors((current) => ({ ...current, database: undefined }))
  }
  const changeSchema = (value: string) => {
    setSchema(value)
    setConfirming(false)
  }
  const changeAlias = (value: string) => {
    setAlias(value)
    setAliasTouched(true)
    setConfirming(false)
    setErrors((current) => ({ ...current, alias: undefined }))
  }
  const pick = (option: DbScopeOption) => {
    changeDatabase(option.database ?? '')
    changeSchema(option.schema ?? '')
  }

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    if (!connection) return
    const found: FieldErrors<ScopeField> = {}
    if (!isSqlite) {
      const databaseError = requiredError('Database', database)
      if (databaseError) found.database = databaseError
    }
    const aliasError = requiredError('Alias', shownAlias)
    if (aliasError) found.alias = aliasError
    setErrors(found)
    if (hasErrors(found)) return
    if (editing && !confirming) {
      setConfirming(true)
      return
    }
    const chosenAlias = shownAlias.trim()
    const req: DbProjectScopeRequest = {
      database: isSqlite ? null : database.trim(),
      schema: isPostgres ? schema.trim() || null : null,
    }
    // Untouched alias: the request leaves it out and the server proposes it, since only the
    // server knows the aliases in use and the rules they follow.
    if (aliasTouched) req.alias = chosenAlias
    setSaving(true)
    setError(null)
    try {
      if (editing && editing.scope_id != null) await api.projects.updateDatabase(projectId, editing.scope_id, req)
      else await api.projects.addDatabase(projectId, connection.id, req)
      toast.success(editing ? `Scope of "${chosenAlias}" changed` : `Data source "${chosenAlias}" added to the project`)
      onClose()
      onSaved()
    } catch (err) {
      setError(actionFailedSentence(editing ? 'change the scope' : 'add the data source', err))
      setConfirming(false)
    } finally {
      setSaving(false)
    }
  }

  const title = editing ? `Change scope of ${editing.alias ?? editing.name}` : 'Add data source'
  const description = connection
    ? 'Choose the database and schema this project works on.'
    : 'Choose a database connection from the organization catalog.'
  const primaryLabel = editing ? (confirming ? 'Confirm scope change' : 'Save scope') : 'Add data source'

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => !o && onClose()}
      title={title}
      description={description}
      maxWidth="560px"
    >
      {connection == null ? (
        <>
          <Input
            id={`${ids}-search`}
            label="Search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Name, engine or host"
          />
          <div className="mt-3 max-h-80 overflow-y-auto border border-border rounded">
            {visible.length === 0 ? (
              <p className="p-4 text-sm text-muted">Nothing in the catalog matches.</p>
            ) : (
              visible.map((c) => {
                const locked = c.restricted && !allowRestricted
                return (
                  <button
                    key={c.id}
                    type="button"
                    disabled={locked}
                    onClick={() => choose(c)}
                    className={[
                      'flex w-full items-start gap-3 px-3 py-2 border-b border-border last:border-b-0 text-sm text-left',
                      locked ? 'text-muted cursor-not-allowed' : 'hover:bg-surface',
                    ].join(' ')}
                  >
                    <span className="min-w-0 flex-1">
                      <span className="font-medium">{c.name}</span>
                      <span className="block text-xs text-muted font-mono truncate">
                        {c.engine} {endpoint(c)}
                      </span>
                    </span>
                    {locked && <Badge variant="muted">Admin only</Badge>}
                  </button>
                )
              })
            )}
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={onClose}>
              Cancel
            </Button>
          </DialogFooter>
        </>
      ) : (
        <form onSubmit={submit} noValidate className="space-y-3">
          {error && <Banner variant="danger" className="break-words">{error}</Banner>}
          <p className="text-sm">
            <span className="font-medium">{connection.name}</span>{' '}
            <span className="text-muted font-mono">
              {connection.engine} {endpoint(connection)}
            </span>
          </p>
          {isSqlite ? (
            <p className="text-sm text-muted">
              A SQLite file has no database or schema to choose: the project works on the whole file.
            </p>
          ) : (
            <>
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm font-medium">Available scopes</span>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  loading={loadingScopes}
                  onClick={() => void loadScopes(connection.id)}
                >
                  {!loadingScopes && <RefreshCw className="w-3.5 h-3.5" />} Refresh
                </Button>
              </div>
              {loadingScopes && available == null && (
                <p className="text-sm text-muted">Reading the scopes from the server…</p>
              )}
              {available && !available.live && (
                <Banner variant="warning">
                  {available.error ? `The server did not answer: ${available.error}. ` : ''}
                  {available.checked_at
                    ? `Showing the scopes last seen ${new Date(available.checked_at).toLocaleString()}.`
                    : 'No scopes have been seen on this connection yet.'}{' '}
                  You can still enter the scope by hand.
                </Banner>
              )}
              {available && available.live && available.scopes.length === 0 && (
                <p className="text-sm text-muted">The server lists no database or schema visible to this user.</p>
              )}
              {available && available.scopes.length > 0 && (
                <div className="max-h-60 overflow-y-auto border border-border rounded">
                  {available.scopes.map((option) => {
                    const key = scopeKey(option.database, option.schema)
                    const used = inUse.has(key)
                    const selected = !used && key === chosenKey
                    return (
                      <button
                        key={key}
                        type="button"
                        aria-pressed={selected}
                        disabled={used}
                        onClick={() => pick(option)}
                        className={[
                          'flex w-full items-center justify-between gap-3 px-3 py-2 border-b border-border last:border-b-0 text-sm text-left font-mono',
                          used ? 'text-muted cursor-not-allowed' : selected ? 'bg-surface font-medium' : 'hover:bg-surface',
                        ].join(' ')}
                      >
                        <span className="truncate">{option.label}</span>
                        {used && <Badge variant="muted">In use</Badge>}
                        {selected && <Check className="w-3.5 h-3.5 text-accent" aria-hidden="true" />}
                      </button>
                    )
                  })}
                </div>
              )}
              <Input
                id={`${ids}-database`}
                label="Database"
                value={database}
                onChange={(e) => changeDatabase(e.target.value)}
                placeholder="database name"
                required
                error={errors.database}
              />
              {isPostgres && (
                <Input
                  id={`${ids}-schema`}
                  label="Schema"
                  value={schema}
                  onChange={(e) => changeSchema(e.target.value)}
                  placeholder="public"
                  hint="Empty means public."
                />
              )}
            </>
          )}
          <Input
            id={`${ids}-alias`}
            label="Alias"
            value={shownAlias}
            onChange={(e) => changeAlias(e.target.value)}
            required
            maxLength={100}
            error={errors.alias}
            hint="The name pages and agents use for this data source in the project."
          />
          {confirming && (
            <Banner variant="warning">
              Agents of this project will work on {label} of {connection.name} from now on. Queries that name the
              previous scope may stop working.
            </Banner>
          )}
          <DialogFooter>
            {!editing && (
              <Button type="button" variant="ghost" onClick={backToCatalog}>
                Back
              </Button>
            )}
            <Button type="button" variant="ghost" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" loading={saving}>
              {primaryLabel}
            </Button>
          </DialogFooter>
        </form>
      )}
    </Dialog>
  )
}
