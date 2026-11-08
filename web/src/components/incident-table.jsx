import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from '@/components/ui/table'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { SignalIcon } from '@/components/signal-icon'
import { StateBadge } from '@/components/state-badge'
import {
  signalMeta,
  detailSummary,
  relTime,
  absTime,
  isActive,
} from '@/lib/domain'
import { cn } from '@/lib/utils'

export function IncidentTable({ incidents, onOpen, focusedId }) {
  return (
    <div className="glass overflow-hidden rounded-lg">
      <Table>
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            <TableHead className="label-eyebrow">Problem</TableHead>
            <TableHead className="label-eyebrow">Container</TableHead>
            <TableHead className="label-eyebrow hidden sm:table-cell">
              Evidence
            </TableHead>
            <TableHead className="label-eyebrow">State</TableHead>
            <TableHead className="label-eyebrow hidden text-right md:table-cell">
              Source
            </TableHead>
            <TableHead className="label-eyebrow text-right">Detected</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {incidents.map((i) => {
            const meta = signalMeta(i.signal?.type)
            const summary = detailSummary(i.signal)
            return (
              <TableRow
                key={i.id}
                id={`inc-${i.id}`}
                onClick={() => onOpen(i)}
                aria-current={i.id === focusedId || undefined}
                className={cn(
                  'scroll-mt-4 cursor-pointer',
                  !isActive(i) && 'opacity-60 hover:opacity-100',
                  i.id === focusedId &&
                    'bg-accent/50 opacity-100 ring-1 ring-inset ring-ring/60',
                )}
              >
                <TableCell>
                  <div className="flex items-center gap-2.5">
                    <SignalIcon type={i.signal?.type} size="sm" />
                    <span className="text-sm" style={{ color: `var(--${meta.token})` }}>{meta.short}</span>
                  </div>
                </TableCell>
                <TableCell className="font-data font-medium text-foreground">
                  {i.target}
                </TableCell>
                <TableCell className="font-data hidden text-muted-foreground sm:table-cell">
                  {summary || ''}
                </TableCell>
                <TableCell>
                  <StateBadge incident={i} />
                </TableCell>
                <TableCell className="font-data hidden text-right text-muted-foreground md:table-cell">
                  {i.signal?.source}
                </TableCell>
                <TableCell className="text-right">
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <span className="font-data text-sm tabular-nums text-muted-foreground">
                        {relTime(i.created_at)}
                      </span>
                    </TooltipTrigger>
                    <TooltipContent>{absTime(i.created_at)}</TooltipContent>
                  </Tooltip>
                </TableCell>
              </TableRow>
            )
          })}
        </TableBody>
      </Table>
    </div>
  )
}
