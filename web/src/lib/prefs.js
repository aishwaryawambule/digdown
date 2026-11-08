import { useCallback, useState } from 'react'

// Remembers how you like a page laid out. Browser storage can be missing or blocked,
// and then this simply starts from the default each time.
export function readPref(storage, key, allowed, fallback) {
  try {
    const value = storage?.getItem(`digdown-${key}`)
    return allowed.includes(value) ? value : fallback
  } catch {
    return fallback
  }
}

export function usePref(key, allowed, fallback) {
  const [value, setValue] = useState(() =>
    readPref(typeof localStorage === 'undefined' ? null : localStorage, key, allowed, fallback))
  const set = useCallback((next) => {
    setValue(next)
    try {
      localStorage.setItem(`digdown-${key}`, next)
    } catch {
      // Not saved, but the page still changes.
    }
  }, [key])
  return [value, set]
}
