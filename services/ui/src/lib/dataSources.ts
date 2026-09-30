import type { DbConnection } from './api'

// Detail page of a project scope.
export function dataSourcePath(source: Pick<DbConnection, 'scope_id'>): string {
  return `/datasources/${source.scope_id}`
}

// Scope opened from an address: the scope id, or the id of a connection that has a single
// scope in the project (addresses saved before scopes, environment links). With several
// scopes of the same connection it does not guess.
export function findDataSource(sources: DbConnection[], ref: number): DbConnection | null {
  const byScope = sources.find((s) => s.scope_id === ref)
  if (byScope) return byScope
  const byConnection = sources.filter((s) => s.id === ref)
  return byConnection.length === 1 ? byConnection[0] : null
}
