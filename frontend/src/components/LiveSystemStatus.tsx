'use client'

import { useQuery } from '@tanstack/react-query'
import { CheckCircle, AlertCircle, Clock } from 'lucide-react'

interface HealthData {
  status: string
  service?: string
}

interface AutopilotData {
  enabled: boolean
  trained: boolean
  mode: string
}

async function fetchHealth(): Promise<HealthData> {
  const res = await fetch('/api/v1/health')
  if (!res.ok) throw new Error('Health check failed')
  return res.json()
}

async function fetchAutopilotStatus(): Promise<AutopilotData | null> {
  try {
    const res = await fetch('/api/v1/autopilot/status')
    if (!res.ok) return null
    return res.json()
  } catch {
    return null
  }
}

export default function LiveSystemStatus() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['health'],
    queryFn: fetchHealth,
    refetchInterval: 60_000,
    retry: 1,
  })

  const { data: autopilot } = useQuery({
    queryKey: ['health-autopilot'],
    queryFn: fetchAutopilotStatus,
    refetchInterval: 60_000,
    retry: 1,
  })

  if (isLoading) return (
    <div className="flex items-center gap-2 text-gray-500 text-sm">
      <Clock className="h-4 w-4 animate-spin" />
      Chargement du statut système...
    </div>
  )

  if (isError || !data) return (
    <div className="flex items-center gap-2 text-red-400 text-sm">
      <AlertCircle className="h-4 w-4" />
      Impossible de récupérer le statut système
    </div>
  )

  const isHealthy = data.status === 'healthy' || data.status === 'ok'

  const items = [
    {
      label: 'Système',
      ok: isHealthy,
      value: isHealthy ? 'Opérationnel' : 'Dégradé',
    },
    {
      label: 'Autopilot',
      ok: true,
      value: autopilot?.enabled
        ? `Activé (${autopilot.mode})`
        : 'Désactivé',
    },
  ]

  return (
    <div className="flex flex-wrap gap-4">
      {items.map((item) => (
        <div key={item.label} className="flex items-center gap-1.5">
          {item.ok
            ? <CheckCircle className="h-4 w-4 text-green-400 shrink-0" />
            : <AlertCircle className="h-4 w-4 text-yellow-400 shrink-0" />
          }
          <span className="text-xs text-gray-400">{item.label}:</span>
          <span className={`text-xs font-medium ${item.ok ? 'text-gray-200' : 'text-yellow-300'}`}>
            {item.value}
          </span>
        </div>
      ))}
    </div>
  )
}
