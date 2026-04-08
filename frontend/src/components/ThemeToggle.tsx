'use client'

import { Sun, Moon } from 'lucide-react'
import { useTheme } from '@/lib/theme'

export function ThemeToggle() {
  const { theme, toggle } = useTheme()

  return (
    <button
      onClick={toggle}
      title={theme === 'dark' ? 'Mode clair' : 'Mode sombre'}
      className="fixed bottom-6 right-6 z-50 p-3 rounded-full bg-ev-surface border border-ev-bd shadow-lg hover:shadow-xl transition-all duration-200 text-ev-t3 hover:text-ev-t1"
    >
      {theme === 'dark'
        ? <Sun className="w-4 h-4" />
        : <Moon className="w-4 h-4" />
      }
    </button>
  )
}
