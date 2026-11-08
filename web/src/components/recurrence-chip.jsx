import { recurrenceChip, relTime } from '@/lib/domain'
import { cn } from '@/lib/utils'

// Inline recurrence marker. Renders only for repeat/chronic targets — absence
// is the signal that an incident is the first of its kind. `record` comes from
// domain.trackRecord(); all presentation logic lives in domain.recurrenceChip().
export function RecurrenceChip({ record, onClick, className }) {
  const chip = recurrenceChip(record)
  if (!chip.show) return null
  const Tag = onClick ? 'button' : 'span'
  return (
    <Tag
      type={onClick ? 'button' : undefined}
      onClick={onClick}
      title={
        record?.lastSeenIso
          ? `Failed ${record.occurrencesTotal} times. Last time was ${relTime(record.lastSeenIso)} ago.`
          : undefined
      }
      className={cn(
        'inline-flex items-center gap-1 rounded-sm border px-1.5 py-px text-xs leading-none font-medium',
        onClick && 'transition-[filter] hover:brightness-110',
        className,
      )}
      style={{
        color: chip.tone,
        borderColor: `color-mix(in oklch, ${chip.tone} 30%, transparent)`,
        backgroundColor: `color-mix(in oklch, ${chip.tone} 10%, transparent)`,
      }}
    >
      {chip.text}
    </Tag>
  )
}
