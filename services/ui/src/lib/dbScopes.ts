import type { DbConnection, DbEngine } from './api'

// Same scope reading as the server (scopes.scope_label): database.schema on PostgreSQL
// (empty schema = public), the database on MySQL and MariaDB, no choice on SQLite.
export function scopeLabel(engine: DbEngine, database?: string | null, schema?: string | null): string {
  if (engine === 'sqlite') return '(file)'
  const db = (database ?? '').trim()
  if (!db) return '(default database)'
  if (engine === 'postgresql') return `${db}.${(schema ?? '').trim() || 'public'}`
  return db
}

// Preview of the alias the server will propose (project_scopes.default_alias): the connection
// name for the project's first link, name/scope for later ones, with a numeric suffix when
// taken (case-insensitive). Only a preview: while the user leaves it untouched the request
// carries no alias and the server has the last word.
export function proposeAlias(projectScopes: DbConnection[], connection: DbConnection, label: string): string {
  const first = !projectScopes.some((s) => s.id === connection.id)
  const base = first ? connection.name : `${connection.name}/${label}`
  const taken = new Set(projectScopes.map((s) => (s.alias ?? s.name).toLowerCase()))
  if (!taken.has(base.toLowerCase())) return base
  let suffix = 2
  while (taken.has(`${base}-${suffix}`.toLowerCase())) suffix += 1
  return `${base}-${suffix}`
}
