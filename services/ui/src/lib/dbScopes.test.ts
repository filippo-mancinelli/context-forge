import { describe, expect, it } from 'vitest'
import type { DbConnection } from './api'
import { proposeAlias, scopeLabel } from './dbScopes'

const erp = { id: 7, name: 'erp', engine: 'postgresql' } as DbConnection

describe('scopeLabel', () => {
  it('reads the scope as the server writes it', () => {
    expect(scopeLabel('postgresql', 'app', 'sales')).toBe('app.sales')
    expect(scopeLabel('postgresql', 'app', '')).toBe('app.public')
    expect(scopeLabel('mysql', 'crm', null)).toBe('crm')
    expect(scopeLabel('sqlite', null, null)).toBe('(file)')
    expect(scopeLabel('mariadb', '', null)).toBe('(default database)')
  })
})

describe('proposeAlias', () => {
  it('uses the connection name for the first link of the project', () => {
    expect(proposeAlias([], erp, 'app.public')).toBe('erp')
  })

  it('adds the scope label to later links of the same connection', () => {
    const linked = { ...erp, scope_id: 3, alias: 'erp' } as DbConnection
    expect(proposeAlias([linked], erp, 'app.sales')).toBe('erp/app.sales')
  })

  it('adds a suffix when the alias is taken, ignoring case', () => {
    const other = { id: 9, name: 'other', engine: 'mysql', scope_id: 5, alias: 'ERP' } as DbConnection
    expect(proposeAlias([other], erp, 'app.public')).toBe('erp-2')
  })
})
