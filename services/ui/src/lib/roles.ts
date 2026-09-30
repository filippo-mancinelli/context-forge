import type { OrgRole } from './api'

const RANK: Record<string, number> = { viewer: 0, member: 1, admin: 2, owner: 3 }

export function roleAtLeast(role: string | undefined, minimum: OrgRole): boolean {
  if (!role || !(role in RANK)) return false
  return RANK[role] >= RANK[minimum]
}

// Il catalogo porta credenziali: lo modificano solo gli admin dell'organizzazione.
export const canManageCatalog = (orgRole?: string) => roleAtLeast(orgRole, 'admin')

// Selezionare una risorsa per il progetto richiede il ruolo member sul progetto.
export const canSelectResources = (projectRole?: string) => roleAtLeast(projectRole, 'member')

// Le risorse riservate le aggiunge a un progetto solo un admin dell'organizzazione.
export const canSelectRestricted = (orgRole?: string) => roleAtLeast(orgRole, 'admin')
