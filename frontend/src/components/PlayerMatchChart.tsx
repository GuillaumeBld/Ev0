'use client'

import { useState } from 'react'
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Cell,
} from 'recharts'
import { RecentMatch } from '@/lib/api'

type Metric = 'xg' | 'rating' | 'goals' | 'shots' | 'key_pass'

interface PlayerMatchChartProps {
  matches: RecentMatch[]
  metric?: Metric
}

const METRIC_CONFIG: Record<Metric, { label: string; color: string; accessor: (m: RecentMatch) => number | null }> = {
  xg: {
    label: 'xG',
    color: '#10b981',
    accessor: (m) => m.expected_goals,
  },
  rating: {
    label: 'Rating',
    color: '#6366f1',
    accessor: (m) => m.rating,
  },
  goals: {
    label: 'Buts',
    color: '#f59e0b',
    accessor: (m) => m.goals,
  },
  shots: {
    label: 'SoT',
    color: '#3b82f6',
    accessor: (m) => m.shots_on_target,
  },
  key_pass: {
    label: 'Key Pass',
    color: '#8b5cf6',
    accessor: (m) => m.key_pass,
  },
}

function formatDate(dateStr: string | null): string {
  if (!dateStr) return '?'
  const d = new Date(dateStr)
  return d.toLocaleDateString('fr-FR', { day: 'numeric', month: 'short' }).replace('.', '')
}

interface TooltipPayload {
  payload?: {
    opponent: string | null
    minutesPlayed: number | null
    value: number | null
    isHome: boolean | null
  }
}

function CustomTooltip({ active, payload }: { active?: boolean; payload?: TooltipPayload[] }) {
  if (!active || !payload || payload.length === 0) return null
  const d = payload[0].payload
  if (!d) return null
  return (
    <div className="bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-xs shadow-lg">
      <p className="text-white font-medium">
        {d.isHome ? 'vs ' : '@ '}{d.opponent ?? '?'}
      </p>
      <p className="text-gray-400">{d.minutesPlayed ?? 0} min</p>
      <p className="text-green-400 font-mono font-semibold">
        {d.value !== null ? d.value.toFixed(2) : '—'}
      </p>
    </div>
  )
}

export function PlayerMatchChart({ matches, metric: initialMetric = 'xg' }: PlayerMatchChartProps) {
  const [activeMetric, setActiveMetric] = useState<Metric>(initialMetric)

  const config = METRIC_CONFIG[activeMetric]

  // Show last N matches chronologically (oldest first)
  const sorted = [...matches]
    .sort((a, b) => {
      if (!a.event_date) return 1
      if (!b.event_date) return -1
      return new Date(a.event_date).getTime() - new Date(b.event_date).getTime()
    })
    .slice(-10)

  const chartData = sorted.map((m) => ({
    date: formatDate(m.event_date),
    value: config.accessor(m),
    opponent: m.opponent,
    minutesPlayed: m.minutes_played,
    isHome: m.is_home,
  }))

  const maxVal = Math.max(...chartData.map((d) => d.value ?? 0), 0.01)

  return (
    <div className="space-y-3">
      {/* Metric selector */}
      <div className="flex items-center gap-1 flex-wrap">
        {(Object.entries(METRIC_CONFIG) as [Metric, typeof METRIC_CONFIG[Metric]][]).map(
          ([key, cfg]) => (
            <button
              key={key}
              onClick={() => setActiveMetric(key)}
              className={`px-3 py-1 rounded-md text-xs font-medium transition-colors ${
                activeMetric === key
                  ? 'bg-brand-600 text-white'
                  : 'bg-gray-700 text-gray-400 hover:bg-gray-600 hover:text-white'
              }`}
            >
              {cfg.label}
            </button>
          )
        )}
      </div>

      {/* Chart */}
      {chartData.length === 0 ? (
        <p className="text-gray-500 text-sm py-4 text-center">Aucun match disponible</p>
      ) : (
        <ResponsiveContainer width="100%" height={160}>
          <BarChart data={chartData} margin={{ top: 4, right: 4, bottom: 0, left: -20 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#374151" vertical={false} />
            <XAxis
              dataKey="date"
              tick={{ fill: '#9ca3af', fontSize: 10 }}
              axisLine={false}
              tickLine={false}
            />
            <YAxis
              domain={[0, maxVal * 1.2]}
              tick={{ fill: '#9ca3af', fontSize: 10 }}
              axisLine={false}
              tickLine={false}
              tickCount={4}
            />
            <Tooltip content={<CustomTooltip />} cursor={{ fill: 'rgba(255,255,255,0.04)' }} />
            <Bar dataKey="value" radius={[3, 3, 0, 0]}>
              {chartData.map((entry, index) => (
                <Cell
                  key={`cell-${index}`}
                  fill={entry.value !== null && entry.value > 0 ? config.color : '#374151'}
                  opacity={entry.minutesPlayed !== null && entry.minutesPlayed < 45 ? 0.5 : 1}
                />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      )}
    </div>
  )
}
