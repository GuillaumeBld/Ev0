'use client'

import { useState } from 'react'
import { Menu, Zap } from 'lucide-react'
import { Sidebar } from './Sidebar'

interface DashboardShellProps {
  user: any
  children: React.ReactNode
}

export function DashboardShell({ user, children }: DashboardShellProps) {
  const [sidebarOpen, setSidebarOpen] = useState(false)

  return (
    <div className="flex h-screen bg-gray-900">
      <Sidebar
        user={user}
        open={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
      />

      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Mobile top bar */}
        <div className="flex items-center h-14 px-4 bg-gray-800 border-b border-gray-700 md:hidden">
          <button
            onClick={() => setSidebarOpen(true)}
            className="p-2 text-gray-400 hover:text-white -ml-2"
          >
            <Menu className="w-6 h-6" />
          </button>
          <div className="flex items-center ml-3">
            <Zap className="w-6 h-6 text-brand-500" />
            <span className="ml-1.5 text-lg font-bold text-white">Ev0</span>
          </div>
        </div>

        <main className="flex-1 overflow-auto">
          {children}
        </main>
      </div>
    </div>
  )
}
