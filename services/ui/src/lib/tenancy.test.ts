import { describe, it, expect } from 'vitest'
import { tenancyHeaders, resolveActiveProjectId } from './tenancy'

describe('tenancyHeaders', () => {
  it('includes X-Project-Id when a project id is set', () => {
    expect(tenancyHeaders('tok', 1, 5)).toEqual({
      Authorization: 'Bearer tok',
      'X-Org-Id': '1',
      'X-Project-Id': '5',
    })
  })

  it('omits X-Project-Id when the project id is null', () => {
    expect('X-Project-Id' in tenancyHeaders('tok', 1, null)).toBe(false)
  })

  it('returns an empty object when nothing is set', () => {
    expect(tenancyHeaders(null, null, null)).toEqual({})
  })
})

describe('resolveActiveProjectId', () => {
  const projects = [{ id: 10 }, { id: 20 }, { id: 30 }]

  it('keeps the stored id when still present', () => {
    expect(resolveActiveProjectId(projects, 20)).toBe(20)
  })

  it('falls back to the first project when the stored id is gone', () => {
    expect(resolveActiveProjectId(projects, 99)).toBe(10)
  })

  it('falls back to the first project when nothing is stored', () => {
    expect(resolveActiveProjectId(projects, null)).toBe(10)
  })

  it('returns null for an empty list', () => {
    expect(resolveActiveProjectId([], 5)).toBeNull()
  })
})
