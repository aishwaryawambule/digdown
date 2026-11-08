import {
  MemoryStick,
  Gauge,
  OctagonX,
  RefreshCw,
  HeartPulse,
  HardDrive,
  PowerOff,
  Pause,
  CircleDashed,
  AlertTriangle,
  Globe,
  Timer,
  ScrollText,
  Activity,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { signalMeta } from '@/lib/domain'

const ICONS = {
  oom: MemoryStick,
  memory_pressure: Gauge,
  crash: OctagonX,
  restart_loop: RefreshCw,
  unhealthy: HeartPulse,
  disk_full: HardDrive,
  stopped: PowerOff,
  paused: Pause,
  created: CircleDashed,
  // App-level sources (v1)
  http_error: Globe,
  slow_response: Timer,
  log_error_spike: ScrollText,
  metric_breach: Activity,
}

// The colour says how serious it is. No tile behind it: in a dense table, a coloured box
// on every row is noise.
export function SignalIcon({ type, className, size = 'md' }) {
  const meta = signalMeta(type)
  const Icon = ICONS[type] || AlertTriangle
  const icon = size === 'sm' ? 'size-4' : size === 'lg' ? 'size-6' : 'size-5'
  return <Icon className={cn('shrink-0', icon, className)} strokeWidth={1.75} style={{ color: `var(--${meta.token})` }} />
}
