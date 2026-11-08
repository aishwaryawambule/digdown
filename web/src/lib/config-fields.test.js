import { test } from 'node:test'
import assert from 'node:assert/strict'
import { CONFIG_GROUPS, FIELD_META, fieldLabel, groupFields } from './config-fields.js'

test('FIELD_META has a label + input type for mode', () => {
  assert.equal(FIELD_META.mode.input, 'select')
  assert.ok(Array.isArray(FIELD_META.mode.options))
  assert.match(fieldLabel('poll_interval_seconds'), /check every/i)
  assert.equal(fieldLabel('unknown_key'), 'unknown_key') // graceful fallback
  assert.ok(Array.isArray(CONFIG_GROUPS))
})

test('groupFields buckets API fields into groups, unknowns into Other', () => {
  const fields = [
    { key: 'mode', editable: true }, { key: 'verify_polls', editable: true },
    { key: 'compose_path', editable: false }, { key: 'weird', editable: false },
  ]
  const groups = groupFields(fields)
  const detection = groups.find((g) => g.title === 'Mode')
  assert.ok(detection.fields.some((f) => f.key === 'mode'))
  const other = groups[groups.length - 1]
  assert.equal(other.title, 'Other')
  assert.ok(other.fields.some((f) => f.key === 'weird'))
})

test('every auto heal setting is grouped under Auto heal, not listed as file-only', () => {
  const keys = ['auto_heal_stacks', 'auto_heal_all', 'auto_heal_disk_cleanup', 'auto_heal_max_attempts']
  const groups = groupFields(keys.map((key) => ({ key, value: null })))
  assert.deepEqual(groups.map((g) => g.title), ['Auto heal'])
})
