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
  Zap
} from 'lucide-react'
import { clsx } from 'clsx'

interface SidebarProps {
  user: any
}

const navigation = [
  { name: 'Dashboard', href: '/dashboard', icon: LayoutDashboard },
  { name: 'Recommendations', href: '/dashboard/recommendations', icon: Target },
  { name: 'Backtest', href: '/dashboard/backtest', icon: TrendingUp },
  { name: 'History', href: '/dashboard/history', icon: History },
  { name: 'Data Health', href: '/dashboard/health', icon: Activity },
  { name: 'Settings', href: '/dashboard/settings', icon: Settings },
]

export function Sidebar({ user }: SidebarProps) {
  const pathname = usePathname()

  return (
    <div className="flex flex-col w-64 bg-gray-800 border-r border-gray-700">
      {/* Logo */}
      <div className="flex items-center h-16 px-6 border-b border-gray-700">
        <Zap className="w-8 h-8 text-brand-500" />
        <span className="ml-2 text-xl font-bold text-white">Ev0</span>
      </div>

      {/* Navigation */}
      <nav className="flex-1 px-4 py-4 space-y-1">
        {navigation.map((item) => {
          const isActive = pathname === item.href
          return (
            <Link
              key={item.name}
              href={item.href}
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
          <div>
            <p className="text-sm font-medium text-white">{user?.name}</p>
            <p className="text-xs text-gray-400">{user?.email}</p>
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
  )
}
