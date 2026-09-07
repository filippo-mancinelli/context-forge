import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { createDebouncer } from './useDebouncedValue'

// There is no DOM test runner in this project (no jsdom/testing-library), so
// `useDebouncedValue` itself is not rendered here. Instead we exercise the
// timing logic it delegates to, `createDebouncer`, with vitest's fake timers.
describe('createDebouncer', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('applies the value only after the delay elapses', () => {
    const apply = vi.fn()
    const debouncer = createDebouncer<string>(300, apply)

    debouncer.set('a')
    vi.advanceTimersByTime(299)
    expect(apply).not.toHaveBeenCalled()

    vi.advanceTimersByTime(1)
    expect(apply).toHaveBeenCalledTimes(1)
    expect(apply).toHaveBeenCalledWith('a')
  })

  it('resets the timer on repeated calls and only applies the latest value', () => {
    const apply = vi.fn()
    const debouncer = createDebouncer<string>(300, apply)

    debouncer.set('a')
    vi.advanceTimersByTime(200)
    debouncer.set('b')
    vi.advanceTimersByTime(200)
    debouncer.set('c')
    vi.advanceTimersByTime(299)
    expect(apply).not.toHaveBeenCalled()

    vi.advanceTimersByTime(1)
    expect(apply).toHaveBeenCalledTimes(1)
    expect(apply).toHaveBeenCalledWith('c')
  })

  it('cancel() prevents a pending apply from firing', () => {
    const apply = vi.fn()
    const debouncer = createDebouncer<string>(300, apply)

    debouncer.set('a')
    debouncer.cancel()
    vi.advanceTimersByTime(1000)

    expect(apply).not.toHaveBeenCalled()
  })
})
