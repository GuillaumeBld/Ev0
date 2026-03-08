'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { signOut } from 'next-auth/react'
import {
  LayoutDashboard,
  TrendingUp,
  History,
  Settings,
  LogOut,
  Activity,
  Target,
  Zap,
  Users,
  Calendar,
  Calculator,
  Brain,
  BookOpen,
  X
} from 'lucide-react'
import { clsx } from 'clsx'

interface SidebarProps {
  user: any
  open?: boolean
  onClose?: () => void
}

const navigation = [
  { name: 'Dashboard', href: '/dashboard', icon: LayoutDashboard },
  { name: 'Calculateur', href: '/dashboard/calculator', icon: Calculator },
  { name: 'Recommendations', href: '/dashboard/recommendations', icon: Target },
  { name: 'Joueurs', href: '/dashboard/players', icon: Users },
  { name: 'Matchs', href: '/dashboard/matches', icon: Calendar },
  { name: 'Backtest', href: '/dashboard/backtest', icon: TrendingUp },
  { name: 'Historique', href: '/dashboard/history', icon: History },
  { name: 'Autopilot', href: '/dashboard/autopilot', icon: Brain },
  { name: 'Docs', href: '/dashboard/docs', icon: BookOpen },
  { name: 'Santé', href: '/dashboard/health', icon: Activity },
  { name: 'Paramètres', href: '/dashboard/settings', icon: Settings },
]

export function Sidebar({ user, open, onClose }: SidebarProps) {
  const pathname = usePathname()

  return (
    <>
      {/* Mobile backdrop */}
      {open && (
        <div
          className="fixed inset-0 bg-black/50 z-40 md:hidden"
          onClick={onClose}
        />
      )}

      {/* Sidebar */}
      <div className={clsx(
        // Base styles
        'flex flex-col bg-gray-800 border-r border-gray-700',
        // Desktop: static sidebar
        'hidden md:flex w-64',
        // Mobile: fixed overlay drawer
        open && '!fixed inset-y-0 left-0 !flex w-64 z-50 md:!relative md:z-auto'
      )}>
        {/* Logo + mobile close */}
        <div className="flex items-center justify-between h-16 px-6 border-b border-gray-700">
          <div className="flex items-center">
            <Zap className="w-8 h-8 text-brand-500" />
            <span className="ml-2 text-xl font-bold text-white">Ev0</span>
          </div>
          {onClose && (
            <button
              onClick={onClose}
              className="p-1 text-gray-400 hover:text-white md:hidden"
            >
              <X className="w-5 h-5" />
            </button>
          )}
        </div>

        {/* Navigation */}
        <nav className="flex-1 px-4 py-4 space-y-1 overflow-y-auto">
          {navigation.map((item) => {
            const isActive = pathname === item.href
            return (
              <Link
                key={item.name}
                href={item.href}
                onClick={onClose}
                className={clsx(
                  'flex items-center px-3 py-2 rounded-lg transition-colors',
                  isActive
                    ? 'bg-brand-600 text-white'
                    : 'text-gray-300 hover:bg-gray-700 hover:text-white'
                )}
              >
                <item.icon className="w-5 h-5 mr-3" />
                {item.name}
              </Link>
            )
          })}
        </nav>

        {/* User */}
        <div className="p-4 border-t border-gray-700">
          <div className="flex items-center justify-between">
            <div className="min-w-0">
              <p className="text-sm font-medium text-white truncate">{user?.name}</p>
              <p className="text-xs text-gray-400 truncate">{user?.email}</p>
            </div>
            <button
              onClick={() => signOut({ callbackUrl: '/login' })}
              className="p-2 text-gray-400 hover:text-white transition-colors"
              title="Sign out"
            >
              <LogOut className="w-5 h-5" />
            </button>
          </div>
        </div>
      </div>
    </>
  )
}
