import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Database, Pencil, Plus, Unlink } from 'lucide-react'
import { api, type DbConnection } from '../lib/api'
import { useAppStore } from '../store'
import ScopeDialog from '../components/datasources/ScopeDialog'
import RestrictedIcon from '../components/catalog/RestrictedIcon'
import StatusBadge from '../components/catalog/StatusBadge'
import { dataSourcePath } from '../lib/dataSources'
import { canSelectResources, canSelectRestricted } from '../lib/roles'
import { useOrgRole, useProjectRole } from '../lib/useRoles'
import { actionFailed, actionFailedSentence } from '../lib/messages'
import { Badge, Banner, Button, Card, Table, Tbody, Td, Th, Thead, Tr, useConfirm, useToast } from '../components/ui'

// Where the connection's server is: the Scope column says the database.
function target(c: DbConnection): string {
  if (c.engine === 'sqlite') return c.database_name ?? ''
  return `${c.host ?? '?'}${c.port ? `:${c.port}` : ''}`
}

interface DialogState {
  open: boolean
  editing: DbConnection | null
}

export default function DataSources() {
  const confirm = useConfirm()
  const toast = useToast()
  const orgRole = useOrgRole()
  const projectRole = useProjectRole()
  const projectId = useAppStore((s) => s.activeProjectId)
  const canSelect = canSelectResources(projectRole) && projectId != null
  const [connections, setConnections] = useState<DbConnection[]>([])
  const [catalog, setCatalog] = useState<DbConnection[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [dialog, setDialog] = useState<DialogState>({ open: false, editing: null })

  const load = useCallback(async () => {
    try {
      const data = await api.datasources.list()
      setConnections(data.connections)
      setError(null)
    } catch (e) {
      setError(actionFailedSentence('load the data sources', e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const openAdd = async () => {
    try {
      setCatalog((await api.catalog.databases.list()).connections)
      setDialog({ open: true, editing: null })
    } catch (e) {
      toast.error(actionFailed('load the catalog', e))
    }
  }

  const removeFromProject = async (conn: DbConnection) => {
    const scopeId = conn.scope_id
    if (projectId == null || scopeId == null) return
    const name = conn.alias ?? conn.name
    const ok = await confirm({
      title: 'Remove data source from project',
      message: `Remove «${name}» from this project? The connection stays in the organization catalog, with its annotations.`,
      confirmLabel: 'Remove data source',
      onConfirm: () => api.projects.removeDatabase(projectId, scopeId),
    })
    if (ok) {
      toast.success(`Data source "${name}" removed from the project`)
      await load()
    }
  }

  return (
    <div className="p-4 sm:p-8">
      <div className="page-wide">
        <div className="flex items-start justify-between gap-4 mb-6">
          <div>
            <h1>Data Sources</h1>
            <p className="text-muted text-sm">
              Databases this project can query through the db_* MCP tools, each on the database and schema the
              project chose.
            </p>
          </div>
          {canSelect && (
            <Button variant="primary" onClick={openAdd}>
              <Plus className="w-3.5 h-3.5" /> Add data source
            </Button>
          )}
        </div>

        {error && <Banner variant="danger" className="mb-4 break-words">{error}</Banner>}

        {loading ? (
          <p className="text-muted text-sm">Loading...</p>
        ) : connections.length === 0 ? (
          <div className="border border-dashed border-border p-8 text-center text-sm text-muted">
            <Database className="w-6 h-6 mx-auto mb-2 opacity-50" />
            No databases in this project yet. Add one from the organization catalog and choose its scope.
          </div>
        ) : (
          <Card className="overflow-x-auto">
            <Table>
              <Thead>
                <Tr>
                  <Th>Name</Th>
                  <Th>Scope</Th>
                  <Th>Engine</Th>
                  <Th>Target</Th>
                  <Th>Status</Th>
                  <Th>Annotations</Th>
                  {canSelect && <Th className="w-24" />}
                </Tr>
              </Thead>
              <Tbody>
                {connections.map((c) => (
                  <Tr key={c.scope_id ?? c.id}>
                    <Td>
                      <Link to={dataSourcePath(c)} className="text-accent hover:underline font-medium">
                        {c.alias ?? c.name}
                      </Link>
                      {c.restricted && <RestrictedIcon />}
                      {c.alias && c.alias !== c.name && <p className="text-xs text-muted mt-0.5">{c.name}</p>}
                      {c.description && <p className="text-xs text-muted mt-0.5 max-w-xs truncate">{c.description}</p>}
                    </Td>
                    <Td>
                      <span className="flex items-center gap-2">
                        <span className="font-mono text-xs">{c.scope_label ?? ''}</span>
                        {c.scope_inferred && (
                          <span title="Verify the scope">
                            <Badge variant="warning">Inferred</Badge>
                          </span>
                        )}
                      </span>
                    </Td>
                    <Td className="text-xs font-mono">{c.engine}</Td>
                    <Td className="text-xs text-muted font-mono">
                      {target(c)}
                      {c.ssh_machine_name && <span className="block">via {c.ssh_machine_name}</span>}
                    </Td>
                    <Td>
                      <StatusBadge status={c.status} error={c.error_message} />
                    </Td>
                    <Td className="text-xs text-muted">{c.annotation_count ?? 0}</Td>
                    {canSelect && (
                      <Td>
                        <div className="flex items-center gap-1 justify-end">
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => setDialog({ open: true, editing: c })}
                            title="Change scope"
                            aria-label="Change scope"
                          >
                            <Pencil className="w-3.5 h-3.5" />
                          </Button>
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => removeFromProject(c)}
                            title="Remove data source from project"
                            aria-label="Remove data source from project"
                          >
                            <Unlink className="w-3.5 h-3.5" />
                          </Button>
                        </div>
                      </Td>
                    )}
                  </Tr>
                ))}
              </Tbody>
            </Table>
          </Card>
        )}

        {projectId != null && (
          <ScopeDialog
            open={dialog.open}
            projectId={projectId}
            catalog={catalog}
            projectScopes={connections}
            editing={dialog.editing}
            allowRestricted={canSelectRestricted(orgRole)}
            onClose={() => setDialog({ open: false, editing: null })}
            onSaved={load}
          />
        )}
      </div>
    </div>
  )
}
