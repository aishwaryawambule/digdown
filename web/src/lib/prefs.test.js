import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readPref } from './prefs.js'

const store = (value) => ({ getItem: () => value })

test('a saved layout choice is used', () => {
  assert.equal(readPref(store('list'), 'view', ['board', 'list'], 'board'), 'list')
})

test('an unknown saved value falls back', () => {
  assert.equal(readPref(store('nonsense'), 'view', ['board', 'list'], 'board'), 'board')
})

test('blocked or missing storage falls back', () => {
  const blocked = { getItem: () => { throw new Error('denied') } }
  assert.equal(readPref(blocked, 'view', ['board', 'list'], 'board'), 'board')
  assert.equal(readPref(null, 'view', ['board', 'list'], 'board'), 'board')
})
