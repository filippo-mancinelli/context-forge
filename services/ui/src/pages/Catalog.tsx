import { Tabs, TabsContent, TabsList, TabsTrigger } from '../components/ui'
import DatabasesTab from '../components/catalog/DatabasesTab'
import FoldersTab from '../components/catalog/FoldersTab'
import MachinesTab from '../components/catalog/MachinesTab'
import RepositoriesTab from '../components/catalog/RepositoriesTab'
import { canManageCatalog } from '../lib/roles'
import { useOrgRole } from '../lib/useRoles'

export default function Catalog() {
  const canManage = canManageCatalog(useOrgRole())
  return (
    <div className="p-4 sm:p-8">
      <div className="page-wide">
        <div className="mb-6">
          <h1>Catalog</h1>
          <p className="text-sm text-muted">
            Resources registered once for the organization. Each project adds the ones it needs.
            {!canManage && ' Only organization admins can change the catalog.'}
          </p>
        </div>
        <Tabs defaultValue="machines">
          <TabsList>
            <TabsTrigger value="machines">Machines</TabsTrigger>
            <TabsTrigger value="folders">Folders</TabsTrigger>
            <TabsTrigger value="databases">Databases</TabsTrigger>
            <TabsTrigger value="repositories">Repositories</TabsTrigger>
          </TabsList>
          <TabsContent value="machines">
            <MachinesTab canManage={canManage} />
          </TabsContent>
          <TabsContent value="folders">
            <FoldersTab canManage={canManage} />
          </TabsContent>
          <TabsContent value="databases">
            <DatabasesTab canManage={canManage} />
          </TabsContent>
          <TabsContent value="repositories">
            <RepositoriesTab canManage={canManage} />
          </TabsContent>
        </Tabs>
      </div>
    </div>
  )
}
