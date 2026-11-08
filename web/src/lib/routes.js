// Every page has an address, so refresh, Back, bookmarks and shared links all work.
// It lives after the # so the server needs no changes.
const SLUGS = { incidents: 'incidents', heal: 'auto-heal', fleet: 'containers', insights: 'trends', settings: 'settings' }
const SECTIONS = Object.fromEntries(Object.entries(SLUGS).map(([section, slug]) => [slug, section]))

export const SETTINGS_TABS = ['fixes', 'apps', 'alerts', 'ai', 'tuning']

export function parseRoute(hash = '') {
  const [slug, detail] = String(hash).replace(/^#\/?/, '').split('/')
  const section = SECTIONS[slug] || 'incidents'
  const isIncident = section === 'incidents' && /^\d+$/.test(detail || '')
  return {
    section,
    incidentId: isIncident ? Number(detail) : null,
    tab: section === 'settings' && SETTINGS_TABS.includes(detail) ? detail : null,
  }
}

export function routeHash({ section = 'incidents', incidentId = null, tab = null } = {}) {
  const base = `#/${SLUGS[section] || SLUGS.incidents}`
  if (section === 'incidents' && incidentId != null) return `${base}/${incidentId}`
  if (section === 'settings' && tab) return `${base}/${tab}`
  return base
}

/** The token from a sign-in link (`/#token=…`), which Digdown prints when it starts. */
export function tokenFromHash(hash) {
  const match = /^#token=([A-Za-z0-9_-]+)$/.exec(hash || '')
  return match ? match[1] : ''
}
