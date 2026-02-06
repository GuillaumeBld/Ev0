'use client'

import { useQuery } from '@tanstack/react-query'
import { 
  CheckCircle, XCircle, AlertTriangle, RefreshCw,
  Database, Clock, Wifi, Server
} from 'lucide-react'
import { clsx } from 'clsx'

type HealthStatus = 'healthy' | 'degraded' | 'down'

interface ServiceHealth {
  name: string
  status: HealthStatus
  latency?: number
  lastCheck: string
  details?: string
}

interface DataQuality {
  source: string
  lastSync: string
  recordCount: number
  freshness: 'fresh' | 'stale' | 'outdated'
  issues: string[]
}

export default function HealthPage() {
  const { data, isLoading, refetch } = useQuery({
    queryKey: ['health'],
    queryFn: async () => mockHealthData,
    refetchInterval: 30000, // Refresh every 30s
  })

  return (
    <div className="p-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-white">Santé Système</h1>
          <p className="text-gray-400 mt-1">
            Monitoring des services et qualité des données
          </p>
        </div>
        <button
          onClick={() => refetch()}
          className="flex items-center gap-2 px-4 py-2 bg-gray-700 hover:bg-gray-600 text-white rounded-lg transition-colors"
        >
          <RefreshCw className="w-4 h-4" />
          Rafraîchir
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
          Qualité des Données
        </h2>
        <div className="bg-gray-800 rounded-xl overflow-hidden">
          <table className="w-full">
            <thead>
              <tr className="border-b border-gray-700">
                <th className="px-4 py-3 text-left text-sm font-medium text-gray-400">Source</th>
                <th className="px-4 py-3 text-left text-sm font-medium text-gray-400">Dernier Sync</th>
                <th className="px-4 py-3 text-right text-sm font-medium text-gray-400">Records</th>
                <th className="px-4 py-3 text-center text-sm font-medium text-gray-400">Fraîcheur</th>
                <th className="px-4 py-3 text-left text-sm font-medium text-gray-400">Problèmes</th>
              </tr>
            </thead>
            <tbody>
              {data?.dataQuality.map((dq) => (
                <tr key={dq.source} className="border-b border-gray-700/50">
                  <td className="px-4 py-3 text-sm text-white font-medium">{dq.source}</td>
                  <td className="px-4 py-3 text-sm text-gray-300">{dq.lastSync}</td>
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
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Recent Events */}
      <div>
        <h2 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
          <Clock className="w-5 h-5" />
          Événements Récents
        </h2>
        <div className="bg-gray-800 rounded-xl p-4 space-y-3">
          {data?.events.map((event, i) => (
            <div key={i} className="flex items-start gap-3">
              <div className={clsx(
                'w-2 h-2 rounded-full mt-1.5',
                event.level === 'info' ? 'bg-blue-400' :
                event.level === 'warning' ? 'bg-yellow-400' : 'bg-red-400'
              )} />
              <div className="flex-1">
                <p className="text-sm text-white">{event.message}</p>
                <p className="text-xs text-gray-500">{event.timestamp}</p>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
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
        <p>Vérifié: {service.lastCheck}</p>
      </div>
    </div>
  )
}

function FreshnessBadge({ freshness }: { freshness: 'fresh' | 'stale' | 'outdated' }) {
  const config = {
    fresh: { label: 'Frais', color: 'bg-green-500/20 text-green-400' },
    stale: { label: 'Ancien', color: 'bg-yellow-500/20 text-yellow-400' },
    outdated: { label: 'Périmé', color: 'bg-red-500/20 text-red-400' },
  }

  return (
    <span className={clsx('px-2 py-0.5 rounded text-xs', config[freshness].color)}>
      {config[freshness].label}
    </span>
  )
}

const mockHealthData = {
  services: [
    { name: 'API Backend', status: 'healthy' as const, latency: 45, lastCheck: 'il y a 30s' },
    { name: 'PostgreSQL', status: 'healthy' as const, latency: 12, lastCheck: 'il y a 30s' },
    { name: 'Redis', status: 'healthy' as const, latency: 2, lastCheck: 'il y a 30s' },
    { name: 'Odds API', status: 'degraded' as const, latency: 850, lastCheck: 'il y a 30s', details: 'Latence élevée' },
  ],
  dataQuality: [
    { source: 'FBref - Ligue 1', lastSync: 'il y a 2h', recordCount: 1250, freshness: 'fresh' as const, issues: [] },
    { source: 'FBref - Premier League', lastSync: 'il y a 2h', recordCount: 1480, freshness: 'fresh' as const, issues: [] },
    { source: 'Odds - Betclic', lastSync: 'il y a 15m', recordCount: 320, freshness: 'fresh' as const, issues: [] },
    { source: 'Odds - Unibet', lastSync: 'il y a 1h', recordCount: 280, freshness: 'stale' as const, issues: ['Données manquantes pour 3 matchs'] },
    { source: 'Odds - PMU', lastSync: 'il y a 4h', recordCount: 150, freshness: 'outdated' as const, issues: ['Sync échoué', 'Rate limit atteint'] },
  ],
  events: [
    { level: 'info', message: 'Sync FBref terminé - 45 joueurs mis à jour', timestamp: 'il y a 2h' },
    { level: 'warning', message: 'Odds API latence élevée (850ms)', timestamp: 'il y a 30m' },
    { level: 'info', message: 'Recommendations générées - 12 picks VALUE', timestamp: 'il y a 1h' },
    { level: 'error', message: 'PMU sync échoué - rate limit', timestamp: 'il y a 4h' },
  ],
}
