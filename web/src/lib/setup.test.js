import { test } from 'node:test'
import assert from 'node:assert/strict'
import { canTestAi, formFromCurrent, preferredModel, previewLines, setupPayload, SETUP_CHOICES } from './setup.js'

const form = (over = {}) => ({
  scope: 'all', stacks: [], provider: 'ollama', baseUrl: 'http://localhost:11434/v1',
  model: 'qwen2.5:14b', apiKey: '', mode: 'observe', ...over,
})

test('watching everything saves an empty project list', () => {
  assert.deepEqual(setupPayload(form()).project_name, [])
})

test('picked stacks are saved', () => {
  assert.deepEqual(setupPayload(form({ scope: 'some', stacks: ['shop'] })).project_name, ['shop'])
})

test('no AI turns diagnosis off and forces the safest mode', () => {
  const p = setupPayload(form({ provider: 'none', mode: 'execute' }))
  assert.equal(p.agent_enabled, false)
  assert.equal(p.mode, 'observe')
  assert.equal(p.llm_model, undefined)
})

test('Ollama saves its address and sends no key', () => {
  const p = setupPayload(form())
  assert.equal(p.llm_provider, 'openai')
  assert.equal(p.llm_base_url, 'http://localhost:11434/v1')
  assert.equal(p.llm_api_key, undefined)
})

test('Claude API key and setup-token go to different settings', () => {
  const key = setupPayload(form({ provider: 'claude', model: 'm', apiKey: ' sk-ant-api03-x ' }))
  assert.equal(key.llm_provider, 'claude')
  assert.equal(key.llm_api_key, 'sk-ant-api03-x')
  assert.equal(key.llm_base_url, undefined)
  const token = setupPayload(form({ provider: 'claude', model: 'm', apiKey: 'sk-ant-oat01-x' }))
  assert.equal(token.llm_auth_token, 'sk-ant-oat01-x')
  assert.equal(token.llm_api_key, undefined)
})

test('the connection test needs what the provider needs', () => {
  assert.equal(canTestAi(form()), true)
  assert.equal(canTestAi(form({ model: '  ' })), false)
  assert.equal(canTestAi(form({ provider: 'claude', model: 'm' })), false)
  assert.equal(canTestAi(form({ provider: 'claude', model: 'm', apiKey: 'k' })), true)
  assert.equal(canTestAi(form({ provider: 'none' })), false)
})

test('the preview only promises what the chosen mode does', () => {
  assert.equal(previewLines(form({ mode: 'observe' })).action, null)
  assert.equal(previewLines(form({ mode: 'observe' })).fix, null)
  assert.equal(previewLines(form({ mode: 'propose' })).action, null)
  assert.ok(previewLines(form({ mode: 'propose' })).fix)
  assert.equal(previewLines(form({ mode: 'execute' })).action, 'Approve fix')
  assert.match(previewLines(form({ provider: 'none' })).note, /AI model/)
})

test('the preview is honest about where data goes', () => {
  assert.match(previewLines(form()).privacy, /Nothing leaves/)
  assert.match(previewLines(form({ provider: 'claude' })).privacy, /secrets removed/)
})

test('changing an existing setup starts from what is saved', () => {
  const claude = formFromCurrent({ agent_enabled: true, llm_provider: 'claude', llm_model: 'm', mode: 'execute', project_name: ['shop'], has_key: true })
  assert.equal(claude.provider, 'claude')
  assert.equal(claude.scope, 'some')
  assert.equal(canTestAi(claude), true) // the saved key can be used for the test
  assert.equal(formFromCurrent({ agent_enabled: true, llm_provider: 'openai', llm_base_url: 'http://localhost:11434/v1' }).provider, 'ollama')
  assert.equal(formFromCurrent({ agent_enabled: true, llm_provider: 'openai', llm_base_url: 'https://api.openai.com/v1' }).provider, 'compatible')
  assert.equal(formFromCurrent({ agent_enabled: false, mode: 'execute' }).mode, 'observe')
})

test('the suggested model is one that can do the job', () => {
  assert.equal(preferredModel(['bge-m3:latest', 'gemma3:12b', 'qwen3:14b']), 'qwen3:14b')
  assert.equal(preferredModel(['bge-m3:latest', 'gemma3:12b']), 'gemma3:12b')
  assert.equal(preferredModel(['nomic-embed-text']), '')
  assert.equal(preferredModel([]), '')
})

test('"Fix things by itself" allows fixes and turns on auto heal for every app', () => {
  const p = setupPayload(form({ mode: 'auto' }))
  assert.equal(p.mode, 'execute')
  assert.equal(p.auto_heal_all, true)
})

test('every other choice leaves auto heal for every app off', () => {
  for (const mode of ['observe', 'propose', 'execute']) {
    assert.equal(setupPayload(form({ mode })).auto_heal_all, false)
  }
})

test('without an AI, auto heal stays off', () => {
  const p = setupPayload(form({ provider: 'none', mode: 'auto' }))
  assert.equal(p.mode, 'observe')
  assert.notEqual(p.auto_heal_all, true)
})

test('setup offers the four choices, safest first', () => {
  assert.deepEqual(SETUP_CHOICES, ['observe', 'propose', 'execute', 'auto'])
})

test('changing setup shows auto heal for every app as its own choice', () => {
  const f = formFromCurrent({ mode: 'execute', agent_enabled: true, auto_heal_all: true,
    llm_provider: 'claude', project_name: [] })
  assert.equal(f.mode, 'auto')
})

test('the preview for fixing by itself has no button to click', () => {
  const lines = previewLines(form({ mode: 'auto' }))
  assert.equal(lines.action, null)
  assert.match(lines.note, /by itself/)
})
