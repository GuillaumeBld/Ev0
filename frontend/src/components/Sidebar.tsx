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
  ClipboardList,
  Shield,
  X
} from 'lucide-react'
import { clsx } from 'clsx'

interface SidebarProps {
  user: any
  open?: boolean
  onClose?: () => void
}

const navGroups = [
  {
    label: null,
    items: [
      { name: 'Dashboard', href: '/dashboard', icon: LayoutDashboard },
      { name: 'Calculateur', href: '/dashboard/calculator', icon: Calculator },
      { name: 'Recommandations', href: '/dashboard/recommendations', icon: Target },
    ],
  },
  {
    label: 'Analyse',
    items: [
      { name: 'Joueurs', href: '/dashboard/players', icon: Users },
      { name: 'Matchs', href: '/dashboard/matches', icon: Calendar },
      { name: 'Équipes', href: '/dashboard/teams', icon: Shield },
      { name: 'Compos', href: '/dashboard/lineups', icon: ClipboardList },
      { name: 'Backtest', href: '/dashboard/backtest', icon: TrendingUp },
      { name: 'Historique', href: '/dashboard/history', icon: History },
    ],
  },
  {
    label: 'CDM 2026',
    items: [
      { name: 'Compos', href: '/dashboard/wc2026/lineups', icon: ClipboardList },
      { name: 'Pricing', href: '/dashboard/wc2026/pricing', icon: Calculator },
    ],
  },
  {
    label: 'Système',
    items: [
      { name: 'Autopilot', href: '/dashboard/autopilot', icon: Brain },
      { name: 'Docs', href: '/dashboard/docs', icon: BookOpen },
      { name: 'Santé', href: '/dashboard/health', icon: Activity },
      { name: 'Paramètres', href: '/dashboard/settings', icon: Settings },
    ],
  },
]

export function Sidebar({ user, open, onClose }: SidebarProps) {
  const pathname = usePathname()

  return (
    <>
      {/* Mobile backdrop */}
      {open && (
        <div
          className="fixed inset-0 bg-black/60 backdrop-blur-sm z-40 md:hidden"
          onClick={onClose}
        />
      )}

      {/* Sidebar */}
      <div className={clsx(
        'flex flex-col bg-ev-sidebar border-r border-ev-bd2',
        'hidden md:flex w-56',
        open && '!fixed inset-y-0 left-0 !flex w-56 z-50 md:!relative md:z-auto'
      )}>
        {/* Logo + mobile close */}
        <div className="flex items-center justify-between h-14 px-5 border-b border-ev-bd2">
          <div className="flex items-center gap-2">
            <Zap className="w-4 h-4 text-emerald-500" strokeWidth={2.5} />
            <span className="text-base font-bold tracking-tight text-white">Ev0</span>
          </div>
          {onClose && (
            <button
              onClick={onClose}
              className="p-1 text-ev-t4 hover:text-ev-t1 transition-colors md:hidden"
            >
              <X className="w-4 h-4" />
            </button>
          )}
        </div>

        {/* Navigation */}
        <nav className="flex-1 px-3 py-4 space-y-0.5 overflow-y-auto">
          {navGroups.map((group, gi) => (
            <div key={gi} className={gi > 0 ? 'pt-4' : ''}>
              {group.label && (
                <p className="px-3 mb-1.5 text-[9px] font-semibold text-ev-t5 uppercase tracking-[0.18em]">
                  {group.label}
                </p>
              )}
              {group.items.map((item) => {
                const isActive = pathname === item.href
                return (
                  <Link
                    key={item.name}
                    href={item.href}
                    onClick={onClose}
                    className={clsx(
                      'ev0-nav-item flex items-center gap-3 px-3 py-2 rounded-lg text-sm border-l-2',
                      isActive
                        ? 'border-l-emerald-500 bg-[var(--ev-hover)] text-white font-medium'
                        : 'border-l-transparent text-ev-t3 hover:text-ev-t2 hover:bg-[var(--ev-hover)]'
                    )}
                  >
                    <item.icon className="w-4 h-4 shrink-0" strokeWidth={isActive ? 2 : 1.75} />
                    {item.name}
                  </Link>
                )
              })}
            </div>
          ))}
        </nav>

        {/* User */}
        <div className="p-3 border-t border-ev-bd2">
          <div className="flex items-center justify-between px-2 py-1.5">
            <div className="min-w-0">
              <p className="text-xs font-medium text-ev-t2 truncate">{user?.name}</p>
              <p className="text-[10px] text-ev-t4 truncate mt-0.5">{user?.email}</p>
            </div>
            <button
              onClick={() => signOut({ callbackUrl: '/login' })}
              className="p-1.5 text-ev-t4 hover:text-rose-400 transition-colors rounded-md hover:bg-rose-500/10"
              title="Se déconnecter"
            >
              <LogOut className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>
      </div>
    </>
  )
}
