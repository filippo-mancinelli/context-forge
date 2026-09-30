import { describe, expect, it } from 'vitest'
import type { DbConnection } from './api'
import { dataSourcePath, findDataSource } from './dataSources'

const source = (id: number, scopeId: number) => ({ id, scope_id: scopeId }) as DbConnection

describe('data source addresses', () => {
  it('opens the detail page by scope id', () => {
    expect(dataSourcePath(source(42, 7))).toBe('/datasources/7')
  })

  it('matches the scope id before a connection id', () => {
    expect(findDataSource([source(7, 42), source(42, 7)], 7)?.scope_id).toBe(7)
  })

  it('accepts the id of a connection with a single scope', () => {
    expect(findDataSource([source(42, 7)], 42)?.scope_id).toBe(7)
  })

  it('does not guess between scopes of the same connection', () => {
    expect(findDataSource([source(42, 7), source(42, 8)], 42)).toBeNull()
  })
})
