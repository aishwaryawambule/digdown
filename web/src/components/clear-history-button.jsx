import { Trash2 } from 'lucide-react'
import {
  AlertDialog,
  AlertDialogTrigger,
  AlertDialogContent,
  AlertDialogHeader,
  AlertDialogFooter,
  AlertDialogTitle,
  AlertDialogDescription,
  AlertDialogAction,
  AlertDialogCancel,
} from '@/components/ui/alert-dialog'
import { buttonVariants } from '@/components/ui/button'

// Clearing the Done lane permanently deletes the terminal + muted incidents
// server-side, so the trash control opens a modal confirm rather than firing
// straight away. Cancel / Esc / click-outside backs out; active work is never
// touched.
export function ClearHistoryButton({ count, onClear }) {
  const noun = count === 1 ? 'incident' : 'incidents'
  return (
    <AlertDialog>
      <AlertDialogTrigger asChild>
        <button
          type="button"
          className="flex items-center gap-1 rounded text-xs text-muted-foreground transition-colors hover:text-foreground"
        >
          <Trash2 className="size-3" aria-hidden /> Clear
        </button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Clear finished incidents?</AlertDialogTitle>
          <AlertDialogDescription>
            This permanently deletes {count} finished {noun}. Open incidents
            stay as they are.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            className={buttonVariants({ variant: 'destructive' })}
            onClick={onClear}
          >
            Clear {count}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
