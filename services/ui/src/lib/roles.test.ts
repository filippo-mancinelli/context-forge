import { describe, expect, it } from 'vitest'
import { canManageCatalog, canSelectResources, canSelectRestricted, roleAtLeast } from './roles'

describe('roleAtLeast', () => {
  it('orders viewer < member < admin < owner', () => {
    expect(roleAtLeast('admin', 'member')).toBe(true)
    expect(roleAtLeast('viewer', 'member')).toBe(false)
    expect(roleAtLeast(undefined, 'viewer')).toBe(false)
    expect(roleAtLeast('ghost', 'viewer')).toBe(false)
  })
})

describe('catalog rules', () => {
  it('lets only organization admins manage the catalog', () => {
    expect(canManageCatalog('member')).toBe(false)
    expect(canManageCatalog('admin')).toBe(true)
    expect(canManageCatalog('owner')).toBe(true)
  })

  it('lets project members select resources', () => {
    expect(canSelectResources('viewer')).toBe(false)
    expect(canSelectResources('member')).toBe(true)
  })

  it('keeps restricted resources for organization admins', () => {
    expect(canSelectRestricted('member')).toBe(false)
    expect(canSelectRestricted('admin')).toBe(true)
  })
})
