'use client'

import { useState, useEffect } from 'react'
import { Menu } from 'lucide-react'
import { Sidebar } from './Sidebar'
import { XgBadge } from './XgBadge'

interface DashboardShellProps {
  user: any
  children: React.ReactNode
}

export function DashboardShell({ user, children }: DashboardShellProps) {
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [xgMode, setXgMode] = useState<'bzzoiro' | 'model'>('bzzoiro')

  useEffect(() => {
    fetch('/api/config/xg-source')
      .then((r) => r.json())
      .then((d) => {
        if (d.mode === 'bzzoiro' || d.mode === 'model') setXgMode(d.mode)
      })
      .catch(() => { /* non-fatal */ })
  }, [])

  async function handleToggleXgMode() {
    const newMode = xgMode === 'bzzoiro' ? 'model' : 'bzzoiro'
    try {
      await fetch('/api/config/xg-source', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: newMode }),
      })
      setXgMode(newMode)
    } catch {
      /* non-fatal */
    }
  }

  return (
    <div className="flex h-screen bg-ev-bg">
      <Sidebar
        user={user}
        open={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
      />

      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Top bar (mobile hamburger + desktop xG toggle) */}
        <div className="flex items-center h-14 px-4 bg-ev-sidebar border-b border-ev-bd">
          {/* Hamburger — mobile only */}
          <button
            onClick={() => setSidebarOpen(true)}
            className="p-2 text-ev-t3 hover:text-ev-t1 transition-colors -ml-2 md:hidden"
          >
            <Menu className="w-5 h-5" />
          </button>
          <span className="ml-3 text-base font-bold tracking-tight text-white md:hidden">Ev0</span>

          {/* xG source toggle — right side, always visible */}
          <div className="ml-auto flex items-center gap-2">
            <span className="text-[10px] text-ev-t5 uppercase tracking-widest">xG source</span>
            <button
              onClick={handleToggleXgMode}
              title={`Source xG actuelle : ${xgMode}. Cliquer pour basculer.`}
              className="flex items-center gap-1.5 px-2 py-1 rounded-lg border border-ev-bd hover:border-ev-bd bg-transparent transition-colors"
            >
              <XgBadge source={xgMode} />
            </button>
          </div>
        </div>

        <main className="flex-1 overflow-auto">
          {children}
        </main>
      </div>
    </div>
  )
}
