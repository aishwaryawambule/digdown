import { Server } from 'lucide-react'
import { Sparkline } from '@/components/sparkline'
import { hostVital } from '@/lib/domain'

// The box the stack runs on, as a Fleet tile: disk bar + load + mem, with
// disk/mem sparklines. Renders nothing until the host payload arrives.
export function HostCard({ host }) {
  if (!host) return null
  const disk = host.disk_percent
  const high = (disk ?? 0) >= 90
  return (
    <div className="glass-lite rounded-lg px-4 py-3.5">
      <div className="flex items-center gap-2.5">
        <Server className="size-4 text-muted-foreground" />
        <span className="label-eyebrow text-muted-foreground">This computer</span>
        <span className="font-data ml-auto text-sm tabular-nums text-foreground">
          {hostVital(host.load1)} <span className="text-muted-foreground">load</span>
        </span>
      </div>
      <div className="mt-2.5">
        <div className="flex items-center justify-between text-xs text-muted-foreground">
          <span>Disk used</span>
          <span className="font-data tabular-nums">{hostVital(disk, '%')}</span>
        </div>
        <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-muted/40">
          <div
            className="h-full rounded-full"
            style={{ width: `${Math.min(100, disk ?? 0)}%`,
                     background: high ? 'var(--sev-disk)' : 'var(--live)' }}
          />
        </div>
      </div>
      <div className="mt-2.5 flex items-center gap-3">
        <Sparkline label="disk" points={host.disk_series} max={100} color="var(--sev-disk)" />
        <Sparkline label="mem" points={host.mem_series} max={100} color="var(--sev-mempressure)" />
        <span className="font-data ml-auto text-xs tabular-nums text-muted-foreground">
          Memory {hostVital(host.mem_percent, '%')}
        </span>
      </div>
    </div>
  )
}
