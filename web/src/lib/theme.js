// Dark by default, as it's kept open on a second screen, unless the person chose light.
const KEY = 'digdown-theme'

function stored() {
  try {
    return localStorage.getItem(KEY)
  } catch {
    return null
  }
}

export function isDark() {
  const choice = stored()
  return choice ? choice === 'dark' : true
}

export function applyTheme() {
  document.documentElement.classList.toggle('dark', isDark())
  window.dispatchEvent(new Event('digdown-theme'))
}

export function toggleTheme() {
  try {
    localStorage.setItem(KEY, isDark() ? 'light' : 'dark')
  } catch {
    /* private window: the choice lasts until reload */
  }
  applyTheme()
  return isDark()
}

