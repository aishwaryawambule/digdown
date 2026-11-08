import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import Root from './Root.jsx'
import { Toaster } from '@/components/ui/sonner'
import { TooltipProvider } from '@/components/ui/tooltip'
import { applyTheme } from '@/lib/theme'

applyTheme()

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <TooltipProvider delayDuration={150}>
      <Root />
      <Toaster />
    </TooltipProvider>
  </StrictMode>,
)
