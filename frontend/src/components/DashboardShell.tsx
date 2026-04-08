'use client'

import { useState } from 'react'
import { Menu } from 'lucide-react'
import { Sidebar } from './Sidebar'

interface DashboardShellProps {
  user: any
  children: React.ReactNode
}

export function DashboardShell({ user, children }: DashboardShellProps) {
  const [sidebarOpen, setSidebarOpen] = useState(false)

  return (
    <div className="flex h-screen bg-ev-bg">
      <Sidebar
        user={user}
        open={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
      />

      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Mobile top bar */}
        <div className="flex items-center h-14 px-4 bg-ev-sidebar border-b border-ev-bd md:hidden">
          <button
            onClick={() => setSidebarOpen(true)}
            className="p-2 text-ev-t3 hover:text-ev-t1 transition-colors -ml-2"
          >
            <Menu className="w-5 h-5" />
          </button>
          <span className="ml-3 text-base font-bold tracking-tight text-white">Ev0</span>
        </div>

        <main className="flex-1 overflow-auto">
          {children}
        </main>
      </div>
    </div>
  )
}
