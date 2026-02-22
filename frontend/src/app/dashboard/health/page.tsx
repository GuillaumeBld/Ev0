'use client'

import { useQuery } from '@tanstack/react-query'
import {
  CheckCircle, XCircle, AlertTriangle, RefreshCw,
  Database, Clock, Server
} from 'lucide-react'
import { clsx } from 'clsx'

type HealthStatus = 'healthy' | 'degraded' | 'down'

interface ServiceHealth {
  name: string
  status: HealthStatus
  latency?: number
  lastCheck: string
}

interface DataQuality {
  source: string
  lastSync: string | null
  recordCount: number
  freshness: 'fresh' | 'stale' | 'outdated'
  issues: string[]
}

export default function HealthPage() {
  const { data, isLoading, refetch } = useQuery({
    queryKey: ['health'],
    queryFn: async () => {
      try {
        const [healthRes, readyRes, dqRes] = await Promise.all([
          fetch('/health').then(r => r.json()).catch(() => ({ status: 'down' })),
          fetch('/ready').then(r => r.json()).catch(() => ({ status: 'down', checks: {} })),
          fetch('/data-quality').then(r => r.json()).catch(() => []),
        ])

        const dbCheck = readyRes.checks?.db || { status: 'down' }

        const services: ServiceHealth[] = [
          {
            name: 'API Backend',
            status: healthRes.status === 'healthy' ? 'healthy' : 'down',
            lastCheck: 'maintenant',
          },
          {
            name: 'PostgreSQL',
            status: dbCheck.status === 'healthy' ? 'healthy' : 'down',
            latency: dbCheck.latency_ms,
            lastCheck: 'maintenant',
          },
        ]

        const dataQuality: DataQuality[] = (dqRes || []).map((item: any) => ({
          source: item.source,
          lastSync: item.last_sync,
          recordCount: item.record_count,
          freshness: item.freshness as DataQuality['freshness'],
          issues: item.issues || [],
        }))

        return { services, dataQuality }
      } catch {
        return {
          services: [
            { name: 'API Backend', status: 'down' as const, lastCheck: 'maintenant' },
          ],
          dataQuality: [] as DataQuality[],
        }
      }
    },
    refetchInterval: 30000,
  })

  return (
    <div className="p-4 md:p-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-white">Sante Systeme</h1>
          <p className="text-gray-400 mt-1">
            Monitoring des services et qualite des donnees
          </p>
        </div>
        <button
          onClick={() => refetch()}
          className="flex items-center gap-2 px-4 py-2 bg-gray-700 hover:bg-gray-600 text-white rounded-lg transition-colors"
        >
          <RefreshCw className="w-4 h-4" />
          Rafraichir
        </button>
      </div>

      {/* Services Status */}
      <div className="mb-8">
        <h2 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
          <Server className="w-5 h-5" />
          Services
        </h2>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
          {data?.services.map((service) => (
            <ServiceCard key={service.name} service={service} />
          ))}
        </div>
      </div>

      {/* Data Quality */}
      <div className="mb-8">
        <h2 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
          <Database className="w-5 h-5" />
          Qualite des Donnees
        </h2>
        <div className="bg-gray-800 rounded-xl overflow-hidden">
          <table className="w-full">
            <thead>
              <tr className="border-b border-gray-700">
                <th className="px-4 py-3 text-left text-sm font-medium text-gray-400">Source</th>
                <th className="px-4 py-3 text-left text-sm font-medium text-gray-400">Dernier Sync</th>
                <th className="px-4 py-3 text-right text-sm font-medium text-gray-400">Records</th>
                <th className="px-4 py-3 text-center text-sm font-medium text-gray-400">Fraicheur</th>
                <th className="px-4 py-3 text-left text-sm font-medium text-gray-400">Problemes</th>
              </tr>
            </thead>
            <tbody>
              {isLoading ? (
                <tr>
                  <td colSpan={5} className="px-4 py-8 text-center text-gray-400">
                    Chargement...
                  </td>
                </tr>
              ) : data?.dataQuality && data.dataQuality.length > 0 ? (
                data.dataQuality.map((dq) => (
                  <tr key={dq.source} className="border-b border-gray-700/50">
                    <td className="px-4 py-3 text-sm text-white font-medium">{dq.source}</td>
                    <td className="px-4 py-3 text-sm text-gray-300">
                      {dq.lastSync ? formatSyncTime(dq.lastSync) : 'Jamais'}
                    </td>
                    <td className="px-4 py-3 text-sm text-right text-gray-300">
                      {dq.recordCount.toLocaleString()}
                    </td>
                    <td className="px-4 py-3 text-center">
                      <FreshnessBadge freshness={dq.freshness} />
                    </td>
                    <td className="px-4 py-3">
                      {dq.issues.length === 0 ? (
                        <span className="text-sm text-green-400">Aucun</span>
                      ) : (
                        <div className="space-y-1">
                          {dq.issues.map((issue, i) => (
                            <div key={i} className="flex items-center gap-1 text-sm text-yellow-400">
                              <AlertTriangle className="w-3 h-3" />
                              {issue}
                            </div>
                          ))}
                        </div>
                      )}
                    </td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={5} className="px-4 py-8 text-center text-gray-400">
                    Aucune donnee disponible
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Recent Events */}
      <div>
        <h2 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
          <Clock className="w-5 h-5" />
          Evenements Recents
        </h2>
        <div className="bg-gray-800 rounded-xl p-4">
          <p className="text-sm text-gray-500 text-center py-4">
            Aucun evenement recent
          </p>
        </div>
      </div>
    </div>
  )
}

function formatSyncTime(isoStr: string): string {
  try {
    const d = new Date(isoStr)
    return d.toLocaleString('fr-FR', {
      day: '2-digit',
      month: '2-digit',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return isoStr
  }
}

function ServiceCard({ service }: { service: ServiceHealth }) {
  const statusConfig = {
    healthy: { icon: CheckCircle, color: 'text-green-400', bg: 'bg-green-400/10' },
    degraded: { icon: AlertTriangle, color: 'text-yellow-400', bg: 'bg-yellow-400/10' },
    down: { icon: XCircle, color: 'text-red-400', bg: 'bg-red-400/10' },
  }

  const config = statusConfig[service.status]
  const Icon = config.icon

  return (
    <div className={clsx('rounded-xl p-4', config.bg)}>
      <div className="flex items-center justify-between mb-2">
        <span className="font-medium text-white">{service.name}</span>
        <Icon className={clsx('w-5 h-5', config.color)} />
      </div>
      <div className="text-sm text-gray-400">
        {service.latency && <p>Latence: {service.latency}ms</p>}
        <p>Verifie: {service.lastCheck}</p>
      </div>
    </div>
  )
}

function FreshnessBadge({ freshness }: { freshness: 'fresh' | 'stale' | 'outdated' }) {
  const config = {
    fresh: { label: 'Frais', color: 'bg-green-500/20 text-green-400' },
    stale: { label: 'Ancien', color: 'bg-yellow-500/20 text-yellow-400' },
    outdated: { label: 'Perime', color: 'bg-red-500/20 text-red-400' },
  }

  return (
    <span className={clsx('px-2 py-0.5 rounded text-xs', config[freshness].color)}>
      {config[freshness].label}
    </span>
  )
}
