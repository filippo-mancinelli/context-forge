import { useEffect, useState } from 'react'

/**
 * Schedules `apply(value)` to run `delayMs` after the most recent `set()` call,
 * cancelling any call still pending. Factored out of `useDebouncedValue` below
 * so the timing logic can be unit-tested without a DOM (this project has no
 * jsdom/testing-library dependency).
 */
export function createDebouncer<T>(delayMs: number, apply: (value: T) => void) {
  let timer: ReturnType<typeof setTimeout> | undefined

  return {
    set(value: T) {
      if (timer !== undefined) clearTimeout(timer)
      timer = setTimeout(() => {
        timer = undefined
        apply(value)
      }, delayMs)
    },
    cancel() {
      if (timer !== undefined) clearTimeout(timer)
      timer = undefined
    },
  }
}

/**
 * Returns `value`, but only updates the returned value after `value` has
 * stopped changing for `delayMs`. Useful for debouncing text-input driven
 * fetches (e.g. a filter field) without firing a request per keystroke.
 */
export function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value)

  useEffect(() => {
    const debouncer = createDebouncer<T>(delayMs, setDebounced)
    debouncer.set(value)
    return () => debouncer.cancel()
  }, [value, delayMs])

  return debounced
}
