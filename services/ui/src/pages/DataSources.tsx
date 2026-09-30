import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { Database, Plus, Unlink } from 'lucide-react'
import { api, type DbConnection } from '../lib/api'
import { useAppStore } from '../store'
import AddFromCatalogDialog from '../components/catalog/AddFromCatalogDialog'
import RestrictedIcon from '../components/catalog/RestrictedIcon'
import StatusBadge from '../components/catalog/StatusBadge'
import { canSelectResources, canSelectRestricted } from '../lib/roles'
import { useOrgRole, useProjectRole } from '../lib/useRoles'
import { Banner, Button, Card, Table, Tbody, Td, Th, Thead, Tr, useConfirm, useToast } from '../components/ui'

function target(c: DbConnection): string {
  if (c.engine === 'sqlite') return c.database_name ?? ''
  return `${c.host ?? '?'}${c.port ? `:${c.port}` : ''}/${c.database_name ?? ''}`
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
  const [addOpen, setAddOpen] = useState(false)

  const load = useCallback(async () => {
    try {
      const data = await api.datasources.list()
      setConnections(data.connections)
      setError(null)
    } catch (e) {
      setError(String(e))
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
      setAddOpen(true)
    } catch (e) {
      toast.error(String(e))
    }
  }

  const options = useMemo(
    () =>
      catalog.map((c) => ({
        id: c.id,
        name: c.name,
        detail: `${c.engine} ${target(c)}${c.ssh_machine_name ? ` via ${c.ssh_machine_name}` : ''}`,
        restricted: c.restricted,
      })),
    [catalog]
  )
  const selectedIds = useMemo(() => new Set(connections.map((c) => c.id)), [connections])

  const removeFromProject = async (conn: DbConnection) => {
    if (projectId == null) return
    const ok = await confirm({
      title: 'Remove from project',
      message: `Remove «${conn.name}» from this project? It stays in the organization catalog, with its annotations.`,
      confirmLabel: 'Remove',
      onConfirm: () => api.projects.deselectResource(projectId, 'databases', conn.id),
    })
    if (ok) {
      toast.success(`Connection "${conn.name}" removed from the project`)
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
              Databases this project can query through the db_* MCP tools, chosen from the organization catalog.
            </p>
          </div>
          {canSelect && (
            <Button variant="primary" onClick={openAdd}>
              <Plus className="w-3.5 h-3.5" /> Add from catalog
            </Button>
          )}
        </div>

        {error && <Banner variant="danger" className="mb-4 break-words">{error}</Banner>}

        {loading ? (
          <p className="text-muted text-sm">Loading...</p>
        ) : connections.length === 0 ? (
          <div className="border border-dashed border-border p-8 text-center text-sm text-muted">
            <Database className="w-6 h-6 mx-auto mb-2 opacity-50" />
            No databases in this project yet. Add them from the organization catalog.
          </div>
        ) : (
          <Card className="overflow-x-auto">
            <Table>
              <Thead>
                <Tr>
                  <Th>Name</Th>
                  <Th>Engine</Th>
                  <Th>Target</Th>
                  <Th>Status</Th>
                  <Th>Annotations</Th>
                  {canSelect && <Th className="w-16" />}
                </Tr>
              </Thead>
              <Tbody>
                {connections.map((c) => (
                  <Tr key={c.id}>
                    <Td>
                      <Link to={`/datasources/${c.id}`} className="text-accent hover:underline font-medium">
                        {c.name}
                      </Link>
                      {c.restricted && <RestrictedIcon />}
                      {c.description && <p className="text-xs text-muted mt-0.5 max-w-xs truncate">{c.description}</p>}
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
                        <Button size="sm" variant="ghost" onClick={() => removeFromProject(c)} title="Remove from project" aria-label="Remove from project">
                          <Unlink className="w-3.5 h-3.5" />
                        </Button>
                      </Td>
                    )}
                  </Tr>
                ))}
              </Tbody>
            </Table>
          </Card>
        )}

        {projectId != null && (
          <AddFromCatalogDialog
            open={addOpen}
            title="Add databases"
            kind="databases"
            projectId={projectId}
            options={options}
            selectedIds={selectedIds}
            allowRestricted={canSelectRestricted(orgRole)}
            onClose={() => setAddOpen(false)}
            onAdded={load}
          />
        )}
      </div>
    </div>
  )
}
