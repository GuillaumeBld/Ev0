'use client'

import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { TrendingUp, Target, AlertCircle, Download, BarChart3 } from 'lucide-react'
import { clsx } from 'clsx'
import { RecommendationCard } from './RecommendationCard'
import { getRecommendations, getStats, getStatsBreakdown } from '@/lib/api'
import type { BreakdownItem, PnlPoint } from '@/lib/api'

interface DashboardProps {
  user: any
}

function parseOpponent(fixtureName: string, team: string): string {
  const parts = fixtureName.split(' vs ')
  if (parts.length === 2) {
    return parts[0].trim() === team ? parts[1].trim() : parts[0].trim()
  }
  return fixtureName
}

export function Dashboard({ user }: DashboardProps) {
  const { data: recsData, isLoading } = useQuery({
    queryKey: ['dashboard-recommendations'],
    queryFn: async () => {
      const today = new Date().toISOString().split('T')[0]
      const response = await getRecommendations({ date: today, min_edge: 0.05 })
      const recs = (response.recommendations || []).map((rec: any) => ({
        id: rec.id,
        player: rec.player_name,
        team: rec.team,
        opponent: parseOpponent(rec.fixture_name, rec.team),
        market: rec.market_type,
        fairOdds: rec.fair_odds,
        bestOdds: rec.best_odds,
        bookmaker: rec.best_bookmaker,
        edge: rec.edge,
        confidence: rec.confidence,
        kickoff: rec.kickoff_utc,
        explanation: rec.explanation,
      }))
      return recs
    },
  })

  const { data: statsData } = useQuery({
    queryKey: ['dashboard-stats'],
    queryFn: getStats,
  })

  const { data: breakdown } = useQuery({
    queryKey: ['dashboard-breakdown'],
    queryFn: getStatsBreakdown,
  })

  const recommendations = recsData || []
  const avgEdge = recommendations.length > 0
    ? recommendations.reduce((sum: number, r: any) => sum + r.edge, 0) / recommendations.length
    : 0

  const roiValue = statsData && statsData.total_bets > 0
    ? `${(statsData.roi * 100).toFixed(1)}%`
    : '—'

  return (
    <div className="p-4 md:p-8">
      {/* Header */}
      <div className="flex flex-col sm:flex-row items-start justify-between gap-4 mb-10">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-white">
            Bonjour, {user?.name?.split(' ')[0]}
          </h1>
          <p className="text-slate-600 mt-1 text-sm">
            Voici vos recommandations du jour
          </p>
        </div>
        <DownloadCsvButton />
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-10 ev0-stagger">
        <StatCard
          title="Recommandations"
          value={isLoading ? '…' : recommendations.length.toString()}
          subtitle="aujourd'hui"
          icon={Target}
          color="brand"
        />
        <StatCard
          title="Edge Moyen"
          value={isLoading ? '…' : `${(avgEdge * 100).toFixed(1)}%`}
          subtitle="sur les picks actifs"
          icon={TrendingUp}
          color="green"
        />
        <StatCard
          title="Win Rate"
          value={statsData && statsData.total_bets > 0
            ? `${(statsData.win_rate * 100).toFixed(1)}%`
            : '—'}
          subtitle={statsData && statsData.total_bets > 0
            ? `${statsData.wins}W · ${statsData.losses}L`
            : 'aucun historique'}
          icon={TrendingUp}
          color="blue"
        />
        <StatCard
          title="ROI Mensuel"
          value={roiValue}
          subtitle={statsData && statsData.total_bets > 0
            ? `sur ${statsData.total_bets} paris`
            : 'aucun historique'}
          icon={TrendingUp}
          color="purple"
          rawValue={statsData && statsData.total_bets > 0 ? statsData.roi : undefined}
        />
      </div>

      {/* Performance Analytics */}
      {breakdown && (breakdown.by_market.length > 0 || breakdown.pnl_trend.length > 0) && (
        <div className="mb-10">
          <h2 className="text-[10px] font-semibold text-slate-600 uppercase tracking-[0.15em] mb-4 flex items-center gap-2">
            <BarChart3 className="w-3 h-3" />
            Performance
          </h2>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {/* P&L Trend */}
            {breakdown.pnl_trend.length > 0 && (
              <div className="bg-[#0b0e18] border border-white/[0.06] rounded-xl p-5">
                <h3 className="text-[10px] font-semibold text-slate-600 uppercase tracking-[0.12em] mb-5">Courbe P&L</h3>
                <PnlChart data={breakdown.pnl_trend} />
              </div>
            )}

            {/* Breakdown Tables */}
            <div className="space-y-4">
              {breakdown.by_market.length > 0 && (
                <div className="bg-[#0b0e18] border border-white/[0.06] rounded-xl p-5">
                  <h3 className="text-[10px] font-semibold text-slate-600 uppercase tracking-[0.12em] mb-4">Par marché</h3>
                  <BreakdownTable items={breakdown.by_market} />
                </div>
              )}
              {breakdown.by_league.length > 0 && (
                <div className="bg-[#0b0e18] border border-white/[0.06] rounded-xl p-5">
                  <h3 className="text-[10px] font-semibold text-slate-600 uppercase tracking-[0.12em] mb-4">Par ligue</h3>
                  <BreakdownTable items={breakdown.by_league} />
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* Recommendations */}
      <div>
        <h2 className="text-[10px] font-semibold text-slate-600 uppercase tracking-[0.15em] mb-4">
          Picks Value
        </h2>
        {isLoading ? (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {[1, 2].map((i) => (
              <div key={i} className="bg-[#0b0e18] border border-white/[0.06] rounded-xl h-48 animate-pulse" />
            ))}
          </div>
        ) : recommendations.length > 0 ? (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 ev0-stagger">
            {recommendations.slice(0, 4).map((rec: any) => (
              <RecommendationCard key={rec.id} recommendation={rec} />
            ))}
          </div>
        ) : (
          <div className="bg-[#0b0e18] border border-white/[0.06] rounded-xl p-12 text-center">
            <AlertCircle className="w-8 h-8 text-slate-700 mx-auto mb-3" />
            <p className="text-slate-600 text-sm">
              Pas de recommandations pour le moment.
            </p>
          </div>
        )}
      </div>
    </div>
  )
}

function DownloadCsvButton() {
  const [loading, setLoading] = useState(false)

  const handleDownload = async () => {
    setLoading(true)
    try {
      const res = await fetch('/api/v1/players/export')
      if (!res.ok) throw new Error(`HTTP ${res.status}`)

      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `ev0_players_${new Date().toISOString().slice(0, 10)}.csv`
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
    } catch (err) {
      console.error('CSV export failed:', err)
      alert('Export échoué — vérifiez que le backend est démarré.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <button
      onClick={handleDownload}
      disabled={loading}
      className="flex items-center gap-2 px-4 py-2 bg-transparent border border-white/[0.08] hover:border-white/20 disabled:opacity-50 text-slate-400 hover:text-white text-sm font-medium rounded-lg transition-all duration-200"
    >
      <Download className="w-3.5 h-3.5" />
      {loading ? 'Téléchargement…' : 'Exporter CSV'}
    </button>
  )
}

interface StatCardProps {
  title: string
  value: string
  subtitle: string
  icon: any
  color: 'brand' | 'green' | 'blue' | 'purple'
  rawValue?: number
}

function StatCard({ title, value, subtitle, icon: Icon, color, rawValue }: StatCardProps) {
  const valueColorClass = (() => {
    if (value === '—' || value === '…') return 'text-slate-600'
    if (color === 'green') return 'text-emerald-400'
    if (color === 'blue') return 'text-sky-400'
    if (color === 'purple') {
      if (rawValue !== undefined) return rawValue >= 0 ? 'text-emerald-400' : 'text-rose-400'
      return 'text-slate-300'
    }
    return 'text-slate-100'
  })()

  const iconColorClass = {
    brand: 'text-emerald-500',
    green: 'text-emerald-500',
    blue: 'text-sky-500',
    purple: 'text-violet-500',
  }[color]

  return (
    <div className="ev0-stat-card ev0-card-enter bg-[#0b0e18] border border-white/[0.06] rounded-xl p-5">
      <div className="flex items-start justify-between">
        <div className="min-w-0">
          <p className="text-[10px] font-semibold text-slate-600 uppercase tracking-[0.14em]">{title}</p>
          <p className={clsx('text-3xl font-mono font-semibold mt-2 tabular-nums leading-none', valueColorClass)}>
            {value}
          </p>
          <p className="text-[11px] text-slate-700 mt-2">{subtitle}</p>
        </div>
        <div className={clsx('mt-0.5', iconColorClass)}>
          <Icon className="w-4 h-4" strokeWidth={1.75} />
        </div>
      </div>
    </div>
  )
}

function BreakdownTable({ items }: { items: BreakdownItem[] }) {
  return (
    <table className="w-full">
      <thead>
        <tr>
          <th className="text-left pb-2.5 text-[10px] font-semibold text-slate-600 uppercase tracking-[0.1em]">Label</th>
          <th className="text-right pb-2.5 text-[10px] font-semibold text-slate-600 uppercase tracking-[0.1em]">Paris</th>
          <th className="text-right pb-2.5 text-[10px] font-semibold text-slate-600 uppercase tracking-[0.1em]">W/L</th>
          <th className="text-right pb-2.5 text-[10px] font-semibold text-slate-600 uppercase tracking-[0.1em]">P&L</th>
          <th className="text-right pb-2.5 text-[10px] font-semibold text-slate-600 uppercase tracking-[0.1em]">ROI</th>
        </tr>
      </thead>
      <tbody>
        {items.map((item) => (
          <tr key={item.label} className="border-t border-white/[0.04]">
            <td className="py-2.5 text-sm text-slate-300 font-medium capitalize">{item.label.replace('_', ' ')}</td>
            <td className="py-2.5 text-right text-sm text-slate-600 font-mono">{item.bets}</td>
            <td className="py-2.5 text-right text-sm text-slate-600 font-mono">{item.wins}/{item.losses}</td>
            <td className={clsx('py-2.5 text-right text-sm font-mono font-medium', item.pnl >= 0 ? 'text-emerald-400' : 'text-rose-400')}>
              {item.pnl >= 0 ? '+' : ''}{item.pnl.toFixed(2)}
            </td>
            <td className={clsx('py-2.5 text-right text-sm font-mono', item.roi >= 0 ? 'text-emerald-400' : 'text-rose-400')}>
              {(item.roi * 100).toFixed(1)}%
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function PnlChart({ data }: { data: PnlPoint[] }) {
  if (data.length === 0) return null

  const values = data.map((d) => d.cumulative)
  const max = Math.max(...values, 0)
  const min = Math.min(...values, 0)
  const range = max - min || 1
  const height = 120
  const width = 100

  const points = data.map((d, i) => {
    const x = data.length === 1 ? 50 : (i / (data.length - 1)) * width
    const y = height - ((d.cumulative - min) / range) * height
    return `${x},${y}`
  })

  const zeroY = height - ((0 - min) / range) * height

  const lastPoint = data[data.length - 1]
  const isPositive = lastPoint.cumulative >= 0

  const firstX = parseFloat(points[0].split(',')[0])
  const lastX = parseFloat(points[points.length - 1].split(',')[0])
  const areaPoints = `${firstX},${height} ${points.join(' ')} ${lastX},${height}`

  const lineColor = isPositive ? '#10b981' : '#f43f5e'

  return (
    <div>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="w-full"
        preserveAspectRatio="none"
        style={{ height: '140px' }}
      >
        <defs>
          <linearGradient id="ev0-pnl-gradient" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={lineColor} stopOpacity="0.2" />
            <stop offset="100%" stopColor={lineColor} stopOpacity="0" />
          </linearGradient>
        </defs>
        {/* Zero line */}
        <line
          x1="0" y1={zeroY} x2={width} y2={zeroY}
          stroke="rgba(255,255,255,0.06)"
          strokeWidth="0.4"
          strokeDasharray="2,3"
        />
        {/* Area fill */}
        <polygon points={areaPoints} fill="url(#ev0-pnl-gradient)" />
        {/* P&L line */}
        <polyline
          points={points.join(' ')}
          fill="none"
          stroke={lineColor}
          strokeWidth="1.5"
          vectorEffect="non-scaling-stroke"
          className="ev0-pnl-line"
        />
        {/* Last point dot */}
        <circle
          cx={lastX}
          cy={parseFloat(points[points.length - 1].split(',')[1])}
          r="1.5"
          fill={lineColor}
          vectorEffect="non-scaling-stroke"
        />
      </svg>
      <div className="flex justify-between mt-3 text-[11px]">
        <span className="text-slate-700 font-mono">{data[0].date}</span>
        <span className={clsx('font-mono font-semibold', isPositive ? 'text-emerald-400' : 'text-rose-400')}>
          {isPositive ? '+' : ''}{lastPoint.cumulative.toFixed(2)} EUR
        </span>
        <span className="text-slate-700 font-mono">{lastPoint.date}</span>
      </div>
    </div>
  )
}
