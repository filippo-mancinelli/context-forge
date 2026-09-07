import { describe, it, expect } from 'vitest'
import { pendingBadge, formatAge, isExpired } from './approvals'

describe('pendingBadge', () => {
  it('is null when nothing is pending', () => {
    expect(pendingBadge(0)).toBeNull()
  })

  it('shows the exact count up to nine', () => {
    expect(pendingBadge(1)).toBe('1')
    expect(pendingBadge(9)).toBe('9')
  })

  it('caps at 9+', () => {
    expect(pendingBadge(10)).toBe('9+')
    expect(pendingBadge(250)).toBe('9+')
  })

  it('ignores negative or non-finite counts', () => {
    expect(pendingBadge(-3)).toBeNull()
    expect(pendingBadge(Number.NaN)).toBeNull()
  })
})

describe('formatAge', () => {
  const now = Date.parse('2026-09-07T12:00:00Z')

  it('reports fresh requests as just now', () => {
    expect(formatAge('2026-09-07T11:59:30Z', now)).toBe('just now')
  })

  it('reports minutes', () => {
    expect(formatAge('2026-09-07T11:20:00Z', now)).toBe('40m ago')
  })

  it('reports hours', () => {
    expect(formatAge('2026-09-07T04:00:00Z', now)).toBe('8h ago')
  })

  it('reports days', () => {
    expect(formatAge('2026-09-04T12:00:00Z', now)).toBe('3d ago')
  })

  it('handles an unparsable timestamp', () => {
    expect(formatAge('not-a-date', now)).toBe('unknown')
  })
})

describe('isExpired', () => {
  const now = Date.parse('2026-09-07T12:00:00Z')

  it('is true once the expiry has passed', () => {
    expect(isExpired('2026-09-07T11:59:59Z', now)).toBe(true)
  })

  it('is false while it is in the future', () => {
    expect(isExpired('2026-09-14T12:00:00Z', now)).toBe(false)
  })

  it('is false for an unparsable timestamp', () => {
    expect(isExpired('not-a-date', now)).toBe(false)
  })
})
