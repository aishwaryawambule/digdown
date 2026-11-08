import { test } from 'node:test'
import assert from 'node:assert/strict'
import { parseRoute, routeHash, tokenFromHash } from './routes.js'

test('each page has its own address', () => {
  assert.deepEqual(parseRoute('#/containers'), { section: 'fleet', incidentId: null, tab: null })
  assert.deepEqual(parseRoute('#/trends'), { section: 'insights', incidentId: null, tab: null })
  assert.equal(routeHash({ section: 'fleet' }), '#/containers')
  assert.equal(routeHash({ section: 'insights' }), '#/trends')
})

test('an incident can be bookmarked and shared', () => {
  assert.deepEqual(parseRoute('#/incidents/42'), { section: 'incidents', incidentId: 42, tab: null })
  assert.equal(routeHash({ section: 'incidents', incidentId: 42 }), '#/incidents/42')
})

test('a settings tab can be linked to', () => {
  assert.deepEqual(parseRoute('#/settings/alerts'), { section: 'settings', incidentId: null, tab: 'alerts' })
  assert.equal(routeHash({ section: 'settings', tab: 'alerts' }), '#/settings/alerts')
})

test('anything unrecognised lands on incidents rather than a blank page', () => {
  for (const bad of ['', '#', '#/nope', '#/incidents/abc', '#/incidents/-1', 'javascript:alert(1)']) {
    const route = parseRoute(bad)
    assert.equal(route.section, 'incidents')
    assert.equal(route.incidentId, null)
  }
})

test('an unknown settings tab falls back to the first one', () => {
  assert.equal(parseRoute('#/settings/nope').tab, null)
})

test('an address survives a round trip', () => {
  for (const hash of ['#/incidents', '#/incidents/7', '#/containers', '#/trends', '#/settings', '#/settings/tuning']) {
    assert.equal(routeHash(parseRoute(hash)), hash)
  }
})

test('auto heal has its own page', () => {
  assert.deepEqual(parseRoute('#/auto-heal'), { section: 'heal', incidentId: null, tab: null })
  assert.equal(routeHash({ section: 'heal' }), '#/auto-heal')
})

test('each settings tab has an address', () => {
  for (const tab of ['fixes', 'apps', 'alerts', 'ai', 'tuning']) {
    assert.equal(parseRoute(`#/settings/${tab}`).tab, tab)
  }
})

test('a sign-in link gives its token, and nothing else does', () => {
  assert.equal(tokenFromHash('#token=abc-123_x'), 'abc-123_x')
  assert.equal(tokenFromHash('#/incidents'), '')
  assert.equal(tokenFromHash(''), '')
  assert.equal(tokenFromHash('#token='), '')
})
