import { describe, it, expect } from 'vitest'
import { parseRateLimit, RATE_LIMIT_HINT } from './rateLimit'

describe('parseRateLimit', () => {
  it('reads an empty input as unlimited', () => {
    expect(parseRateLimit('')).toEqual({ value: null })
    expect(parseRateLimit('   ')).toEqual({ value: null })
  })

  it('accepts an integer inside the server range', () => {
    expect(parseRateLimit('1')).toEqual({ value: 1 })
    expect(parseRateLimit('60')).toEqual({ value: 60 })
    expect(parseRateLimit('100000')).toEqual({ value: 100000 })
    expect(parseRateLimit(' 42 ')).toEqual({ value: 42 })
  })

  it('rejects zero, negatives and values above the cap', () => {
    expect(parseRateLimit('0')).toEqual({ error: RATE_LIMIT_HINT })
    expect(parseRateLimit('-5')).toEqual({ error: RATE_LIMIT_HINT })
    expect(parseRateLimit('100001')).toEqual({ error: RATE_LIMIT_HINT })
  })

  it('rejects anything that is not a plain integer', () => {
    expect(parseRateLimit('abc')).toEqual({ error: RATE_LIMIT_HINT })
    expect(parseRateLimit('1.5')).toEqual({ error: RATE_LIMIT_HINT })
    expect(parseRateLimit('1e3')).toEqual({ error: RATE_LIMIT_HINT })
    expect(parseRateLimit('12 34')).toEqual({ error: RATE_LIMIT_HINT })
  })
})
