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
      <div className="flex items-center justify-between mb-10">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-white">Santé Système</h1>
          <p className="text-[#4b5563] mt-1 text-sm tracking-wide">
            Monitoring des services et qualité des données
          </p>
        </div>
        <button
          onClick={() => refetch()}
          className="health-refresh-btn flex items-center gap-2 px-4 py-2 bg-transparent border border-white/[0.08] hover:border-white/20 text-gray-500 hover:text-gray-200 rounded-lg transition-all duration-200 text-sm font-medium"
        >
          <RefreshCw className="health-refresh-icon w-3.5 h-3.5" />
          Rafraîchir
        </button>
      </div>

      {/* Services Status */}
      <div className="mb-10">
        <h2 className="text-[10px] font-semibold text-gray-600 uppercase tracking-[0.15em] mb-4 flex items-center gap-2">
          <Server className="w-3 h-3" />
          Services
        </h2>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3">
          {data?.services.map((service) => (
            <ServiceCard key={service.name} service={service} />
          ))}
        </div>
      </div>

      {/* Data Quality */}
      <div className="mb-10">
        <h2 className="text-[10px] font-semibold text-gray-600 uppercase tracking-[0.15em] mb-4 flex items-center gap-2">
          <Database className="w-3 h-3" />
          Qualité des Données
        </h2>
        <div className="bg-[#282b34] border border-white/[0.06] rounded-xl overflow-hidden">
          <table className="w-full">
            <thead>
              <tr className="border-b border-white/[0.06]">
                <th className="px-5 py-3.5 text-left text-[10px] font-semibold text-gray-600 uppercase tracking-[0.12em]">Source</th>
                <th className="px-5 py-3.5 text-left text-[10px] font-semibold text-gray-600 uppercase tracking-[0.12em]">Dernier Sync</th>
                <th className="px-5 py-3.5 text-right text-[10px] font-semibold text-gray-600 uppercase tracking-[0.12em]">Records</th>
                <th className="px-5 py-3.5 text-center text-[10px] font-semibold text-gray-600 uppercase tracking-[0.12em]">Fraîcheur</th>
                <th className="px-5 py-3.5 text-left text-[10px] font-semibold text-gray-600 uppercase tracking-[0.12em]">Problèmes</th>
              </tr>
            </thead>
            <tbody>
              {isLoading ? (
                <tr>
                  <td colSpan={5} className="px-5 py-12 text-center text-gray-700 text-sm">
                    Chargement...
                  </td>
                </tr>
              ) : data?.dataQuality && data.dataQuality.length > 0 ? (
                data.dataQuality.map((dq) => (
                  <tr key={dq.source} className="health-table-row border-b border-white/[0.03] transition-colors duration-150">
                    <td className="px-5 py-3.5 text-sm text-gray-200 font-medium">{dq.source}</td>
                    <td className="px-5 py-3.5 text-xs text-gray-600 font-mono tabular-nums">
                      {dq.lastSync ? formatSyncTime(dq.lastSync) : 'Jamais'}
                    </td>
                    <td className="px-5 py-3.5 text-sm text-right text-gray-400 font-mono tabular-nums">
                      {dq.recordCount.toLocaleString()}
                    </td>
                    <td className="px-5 py-3.5 text-center">
                      <FreshnessBadge freshness={dq.freshness} />
                    </td>
                    <td className="px-5 py-3.5">
                      {dq.issues.length === 0 ? (
                        <span className="text-xs text-emerald-600">Aucun</span>
                      ) : (
                        <div className="space-y-1">
                          {dq.issues.map((issue, i) => (
                            <div key={i} className="flex items-center gap-1.5 text-xs text-amber-500/80">
                              <AlertTriangle className="w-3 h-3 shrink-0" />
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
                  <td colSpan={5} className="px-5 py-12 text-center text-gray-700 text-sm">
                    Aucune donnée disponible
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Recent Events */}
      <div>
        <h2 className="text-[10px] font-semibold text-gray-600 uppercase tracking-[0.15em] mb-4 flex items-center gap-2">
          <Clock className="w-3 h-3" />
          Événements Récents
        </h2>
        <div className="bg-[#282b34] border border-white/[0.06] rounded-xl p-6">
          <p className="text-sm text-gray-700 text-center py-4">
            Aucun événement récent
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
    healthy: {
      icon: CheckCircle,
      color: 'text-emerald-400',
      border: 'border-l-emerald-500/60',
      dot: 'health-dot-pulse bg-emerald-400',
      label: 'text-emerald-400',
    },
    degraded: {
      icon: AlertTriangle,
      color: 'text-amber-400',
      border: 'border-l-amber-500/60',
      dot: 'bg-amber-400',
      label: 'text-amber-400',
    },
    down: {
      icon: XCircle,
      color: 'text-red-400',
      border: 'border-l-red-500/60',
      dot: 'bg-red-400',
      label: 'text-red-400',
    },
  }

  const config = statusConfig[service.status]
  const Icon = config.icon

  return (
    <div className={clsx(
      'health-service-card group relative rounded-xl p-4',
      'bg-[#282b34] border border-white/[0.06] border-l-2',
      config.border,
    )}>
      <div className="flex items-center justify-between mb-3">
        <span className="text-sm font-semibold text-gray-200">{service.name}</span>
        <div className="flex items-center gap-2">
          <span className={clsx('w-1.5 h-1.5 rounded-full', config.dot)} />
          <Icon className={clsx('w-4 h-4', config.color)} />
        </div>
      </div>
      <div className="space-y-0.5">
        {service.latency && (
          <p className="text-xs font-mono text-gray-600">
            Latence: <span className="text-gray-400">{service.latency}ms</span>
          </p>
        )}
        <p className="text-xs font-mono text-gray-700">
          Vérifié: <span className="text-gray-600">{service.lastCheck}</span>
        </p>
      </div>
    </div>
  )
}

function FreshnessBadge({ freshness }: { freshness: 'fresh' | 'stale' | 'outdated' }) {
  const config = {
    fresh: {
      label: 'Frais',
      color: 'bg-emerald-500/[0.08] text-emerald-400 border border-emerald-500/20',
    },
    stale: {
      label: 'Ancien',
      color: 'bg-amber-500/[0.08] text-amber-400 border border-amber-500/20',
    },
    outdated: {
      label: 'Périmé',
      color: 'bg-red-500/[0.08] text-red-400 border border-red-500/20',
    },
  }

  return (
    <span className={clsx(
      'inline-flex items-center px-2.5 py-0.5 rounded-full text-[10px] font-semibold tracking-wide uppercase',
      config[freshness].color,
    )}>
      {config[freshness].label}
    </span>
  )
}
