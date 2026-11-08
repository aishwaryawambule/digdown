// Data-driven Settings form: which fields group where, their labels and input
// types. Pure + unit-tested; the Settings component renders from this.

export const FIELD_META = {
  mode: { label: 'Mode', input: 'select', options: ['observe', 'propose', 'execute'] },
  poll_interval_seconds: { label: 'Check every (seconds)', input: 'number' },
  debounce_polls: { label: 'Failed checks before an incident', input: 'number' },
  restart_loop_threshold: { label: 'Restarts that count as a loop', input: 'number' },
  correlation_window_seconds: { label: 'Link related failures within (seconds)', input: 'number' },
  disk_percent_threshold: { label: 'Warn when disk is this full (%)', input: 'number' },
  mem_percent_threshold: { label: 'Warn when a container nears its memory limit (%)', input: 'number' },
  log_tail_lines: { label: 'Log lines the AI can read', input: 'number' },
  max_tool_calls: { label: 'Checks the AI can run per step', input: 'number' },
  llm_temperature: { label: 'AI creativity', input: 'number' },
  llm_max_tokens: { label: 'Longest AI answer', input: 'number' },
  collect_resource_stats: { label: 'Show CPU and memory use', input: 'toggle' },
  reuse_recurrence_window_hours: { label: 'Reuse earlier diagnoses for (hours)', input: 'number' },
  disk_path: { label: 'Disk to watch', input: 'text' },
  max_critic_rounds: { label: 'Second-opinion retries', input: 'number' },
  reuse_recurring_diagnosis: { label: 'Reuse earlier diagnoses', input: 'toggle' },
  verify_polls: { label: 'Checks after a fix', input: 'number' },
  verify_poll_interval: { label: 'Seconds between those checks', input: 'number' },
  auto_heal_max_attempts: { label: 'Tries per container in 24 hours', input: 'number' },
  auto_heal_all: { label: 'Auto heal every app', input: 'toggle' },
  auto_heal_disk_cleanup: { label: 'Clear unused Docker data when the disk is full', input: 'toggle' },
}

// One-line "what is this" per config key — surfaced as a tooltip on the label so
// the user knows what each setting does without reading the code.
export const FIELD_DESCRIPTIONS = {
  mode: 'How far Digdown may go when something fails.',
  poll_interval_seconds: 'How often Digdown looks at your containers.',
  debounce_polls: 'Raise this if brief blips are creating incidents.',
  restart_loop_threshold: 'How many restarts in a row count as a problem.',
  correlation_window_seconds: 'When one failure causes others, this groups them under the first.',
  disk_percent_threshold: 'Digdown raises an incident when disk use reaches this.',
  mem_percent_threshold: 'Lets you act before the container gets killed.',
  collect_resource_stats: 'Turn off to make each check faster, especially over SSH.',
  max_critic_rounds: 'If a second AI check disagrees, how many times to re-diagnose before giving up.',
  reuse_recurring_diagnosis: 'When the same problem comes back, reuse the earlier answer. This saves AI costs.',
  reuse_recurrence_window_hours: 'How long an earlier answer still counts when a problem returns.',
  verify_polls: 'How many times Digdown checks a container after fixing it.',
  verify_poll_interval: 'The wait between those checks.',
  auto_heal_disk_cleanup: 'Deletes images nothing uses and the build cache, at most once a day. Never containers or volumes. This can\'t be undone, so it\'s off until you turn it on.',
  auto_heal_max_attempts: 'After this many fixes, auto heal stops and asks you instead.',
  db_path: 'SQLite file storing incidents, audit log, diagnoses and plans.',
  compose_path: 'Path to the docker-compose file Digdown watches.',
  project_name: 'Compose project(s) to watch (empty = every container on the host).',
  exclude_projects: 'Compose project(s) Digdown never watches.',
  disk_path: 'The folder whose disk Digdown checks for free space.',
  host: 'Address the API/dashboard server binds to.',
  port: 'Port the API/dashboard server listens on.',
  auth_token: 'Token required to approve/reject when set (secret — set/unset only).',
  notify_webhook_url: 'Where alerts are sent. Set it under Alerts in Settings (secret).',
  agent_enabled: 'Whether diagnosis runs automatically on new incidents.',
  llm_provider: 'LLM backend: openai (OpenAI-compatible, e.g. Ollama) or claude.',
  llm_base_url: 'OpenAI-compatible endpoint URL (openai provider).',
  llm_api_key: 'API key for the LLM backend (secret).',
  llm_auth_token: 'Claude setup-token / auth token (secret).',
  llm_model: 'Model the agents use for diagnosis and planning.',
  llm_temperature: 'Lower gives steadier answers. Only used by OpenAI-compatible services.',
  llm_max_tokens: 'A cap on the length of each answer. Only used by Claude.',
  log_tail_lines: 'More lines can find more, but make each diagnosis slower.',
  max_tool_calls: 'A cap on how much it looks up for each step of a diagnosis.',
  env_path: 'Env file used by set_env_var remediation (default: compose dir /.env).',
  alert_target_label: 'Grafana alert label that names the target app.',
  probes: 'HTTP / log app-level probe configuration.',
}

export const CONFIG_GROUPS = [
  { title: 'Mode', keys: ['mode'] },
  { title: 'Noticing problems', keys: ['poll_interval_seconds', 'debounce_polls', 'restart_loop_threshold', 'correlation_window_seconds', 'disk_percent_threshold', 'mem_percent_threshold'] },
  { title: 'AI checks', keys: ['max_critic_rounds', 'reuse_recurring_diagnosis'] },
  { title: 'After a fix', keys: ['verify_polls', 'verify_poll_interval'] },
  { title: 'Auto heal', keys: ['auto_heal_stacks', 'auto_heal_all', 'auto_heal_disk_cleanup', 'auto_heal_max_attempts'] },
]

export function fieldLabel(key) {
  return FIELD_META[key]?.label || key
}

export function fieldDesc(key) {
  return FIELD_DESCRIPTIONS[key] || ''
}

/** Partition the API's flat field list into display groups; anything not placed
 *  by CONFIG_GROUPS lands in a trailing "Other" group (read-only / startup-only). */
export function groupFields(fields) {
  const byKey = new Map(fields.map((f) => [f.key, f]))
  const placed = new Set()
  const groups = CONFIG_GROUPS.map((g) => {
    const gf = g.keys.map((k) => byKey.get(k)).filter(Boolean)
    gf.forEach((f) => placed.add(f.key))
    return { title: g.title, fields: gf }
  }).filter((g) => g.fields.length > 0)
  const other = fields.filter((f) => !placed.has(f.key))
  if (other.length) groups.push({ title: 'Other', fields: other })
  return groups
}
