import { XCircle } from 'lucide-react'
import { ConfirmAction } from '@/components/confirm-action'

// Modal confirm before closing a single stuck / ended incident to
// closed_no_fix so it drops to the Done lane. Matches the Clear-history
// dialog; Cancel / Esc / click-outside backs out.
export function DismissButton({ onDismiss, disabled }) {
  return (
    <ConfirmAction
      title="Dismiss this incident?"
      description="Closes it without applying a fix. It stays in your history, and won't reopen unless the problem clears and comes back."
      confirmLabel="Dismiss"
      onConfirm={onDismiss}
      destructive
    >
      <button
        type="button"
        disabled={disabled}
        className="flex items-center gap-1 rounded text-sm text-muted-foreground transition-colors hover:text-foreground disabled:opacity-50"
      >
        <XCircle className="size-3" aria-hidden /> Dismiss
      </button>
    </ConfirmAction>
  )
}
