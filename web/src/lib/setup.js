// The setup screen's decisions, kept pure so they can be tested without a browser.

export const MODES = {
  observe: {
    title: 'Explain only',
    short: 'Explain only',
    about: "Shows what broke and why. Never suggests or changes anything. The safest place to start.",
  },
  propose: {
    title: 'Suggest fixes',
    short: 'Suggest fixes',
    about: 'Also suggests a fix for you to carry out yourself. Digdown still changes nothing.',
  },
  execute: {
    title: 'Fix after I approve',
    short: 'Fix after approval',
    about: 'Applies a fix when you click Approve, checks that it worked, and undoes any changes if it didn\'t. Nothing runs without your click unless you turn on auto heal for an app.',
  },
}

// Setup's last question. "auto" isn't a mode of its own: it's "Fix after I approve"
// with auto heal on for every app, which is how most people want to start if they
// want auto heal at all.
export const SETUP_CHOICES = ['observe', 'propose', 'execute', 'auto']

export const AUTO_CHOICE = {
  title: 'Fix things by itself',
  about: "Auto heal for every app, including ones you add later. Crashes, restart loops and running out of memory are fixed without a click, checked, and undone if they don't hold. A wrong setting, a missing secret or a bug still comes to you.",
}

/** The title and description for one of setup's choices. */
export function setupChoice(key) {
  return key === 'auto' ? AUTO_CHOICE : MODES[key]
}

export const PROVIDERS = {
  ollama: {
    title: 'Ollama, on this computer',
    about: 'Free and private. Nothing leaves this computer. Needs a capable machine, and can take several minutes per failure.',
    provider: 'openai', baseUrl: 'http://localhost:11434/v1', model: 'qwen3:14b',
    needsModel: true, needsKey: false, showUrl: true, local: true,
    urlHint: 'The address where Ollama is listening.',
  },
  claude: {
    title: 'Claude',
    about: 'Fast and needs no hardware. Failure details are sent to Anthropic, with secrets removed first.',
    provider: 'claude', baseUrl: '', model: 'claude-sonnet-5-5',
    needsModel: true, needsKey: true, showUrl: false, local: false,
  },
  compatible: {
    title: 'Another service',
    about: 'OpenAI, OpenRouter, LiteLLM, or anything compatible. Failure details are sent there, with secrets removed first.',
    provider: 'openai', baseUrl: 'https://api.openai.com/v1', model: '',
    needsModel: true, needsKey: true, showUrl: true, local: false,
    urlHint: 'The service\'s OpenAI-compatible address, usually ending in /v1.',
  },
  none: {
    title: 'No AI for now',
    about: 'Digdown still tells you what failed and when, but not why.',
    provider: 'openai', baseUrl: '', model: '',
    needsModel: false, needsKey: false, showUrl: false, local: true,
  },
}

/** Which installed model to suggest. Alphabetical order could easily pick an
 *  embedding model, which can't do this job at all. */
export function preferredModel(models = []) {
  const usable = models.filter((m) => !/embed|bge|minilm|nomic/i.test(m))
  return usable.find((m) => /qwen/i.test(m)) || usable.find((m) => /llama3|tools/i.test(m)) || usable[0] || ''
}

/** A Claude setup-token authenticates differently from an API key. */
function claudeCredential(key) {
  return key.startsWith('sk-ant-oat') ? { llm_auth_token: key } : { llm_api_key: key }
}

/** The form as the settings Digdown saves. */
export function setupPayload(form) {
  const noAi = form.provider === 'none'
  const auto = !noAi && form.mode === 'auto'
  const base = {
    mode: noAi ? 'observe' : auto ? 'execute' : form.mode,
    project_name: form.scope === 'some' ? form.stacks : [],
    agent_enabled: !noAi,
    auto_heal_all: auto,
  }
  if (form.provider === 'none') return base
  const p = PROVIDERS[form.provider]
  const key = (form.apiKey || '').trim()
  const payload = { ...base, llm_provider: p.provider, llm_model: (form.model || '').trim() }
  if (p.showUrl) payload.llm_base_url = (form.baseUrl || '').trim()
  if (key) Object.assign(payload, p.provider === 'claude' ? claudeCredential(key) : { llm_api_key: key })
  return payload
}

export function canTestAi(form) {
  const p = PROVIDERS[form.provider]
  if (!p?.needsModel || !(form.model || '').trim()) return false
  if (p.showUrl && !(form.baseUrl || '').trim()) return false
  return !p.needsKey || Boolean((form.apiKey || '').trim()) || Boolean(form.hasSavedKey)
}

/** The form filled in from what's already saved, for changing an existing setup. */
export function formFromCurrent(current) {
  if (!current) return {}
  const stacks = current.project_name || []
  const mode = current.mode === 'execute' && current.auto_heal_all ? 'auto' : current.mode
  const base = { mode, scope: stacks.length ? 'some' : 'all', stacks }
  if (!current.agent_enabled) return { ...base, provider: 'none', mode: 'observe' }
  const claude = ['claude', 'anthropic'].includes(current.llm_provider)
  const local = !claude && /(localhost|127\.0\.0\.1|host\.docker\.internal):11434/.test(current.llm_base_url || '')
  return {
    ...base,
    provider: claude ? 'claude' : local ? 'ollama' : 'compatible',
    baseUrl: current.llm_base_url || '',
    model: current.llm_model || '',
    hasSavedKey: Boolean(current.has_key),
  }
}

/** What the person will actually see for a failure, given their choices. */
export function previewLines(form) {
  const p = PROVIDERS[form.provider]
  const privacy = p.local
    ? 'Nothing leaves this computer.'
    : 'Failure details go to the service you chose, with secrets removed first. Nothing goes anywhere else.'
  if (form.provider === 'none') {
    return {
      finding: 'The container stopped with exit code 137.',
      fix: null, action: null,
      note: 'Add an AI model in Settings to find out why.',
      privacy,
    }
  }
  const finding = 'Redis ran out of memory during a bulk import and hit its 256 MB limit.'
  if (form.mode === 'observe') {
    return { finding, fix: null, action: null, note: 'Digdown explains and stops there.', privacy }
  }
  if (form.mode === 'auto') {
    return {
      finding, fix: 'Fixed by itself: raised the memory limit to 512 MB and restarted it. It has stayed healthy since.',
      action: null,
      note: "Digdown fixed it by itself and told you. If a fix doesn't hold, it's undone and comes to you.",
      privacy,
    }
  }
  const fix = 'Suggested fix: raise the memory limit to 512 MB and restart.'
  if (form.mode === 'propose') {
    return { finding, fix, action: null, note: 'You carry out the fix yourself.', privacy }
  }
  return {
    finding, fix, action: 'Approve fix',
    note: "If it doesn't work, Digdown undoes it.",
    privacy,
  }
}
