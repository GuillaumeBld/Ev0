'use client'

import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { TrendingUp, Target, AlertCircle, Download } from 'lucide-react'
import { RecommendationCard } from './RecommendationCard'
import { getRecommendations } from '@/lib/api'

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
      const response = await getRecommendations({ min_edge: 0.05 })
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

  const recommendations = recsData || []
  const avgEdge = recommendations.length > 0
    ? recommendations.reduce((sum: number, r: any) => sum + r.edge, 0) / recommendations.length
    : 0

  return (
    <div className="p-8">
      {/* Header */}
      <div className="flex items-start justify-between mb-8">
        <div>
          <h1 className="text-3xl font-bold text-white">
            Bonjour, {user?.name}
          </h1>
          <p className="text-gray-400 mt-1">
            Voici vos recommandations du jour
          </p>
        </div>
        <DownloadCsvButton />
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6 mb-8">
        <StatCard
          title="Recommandations"
          value={isLoading ? '...' : recommendations.length.toString()}
          subtitle="aujourd'hui"
          icon={Target}
          color="brand"
        />
        <StatCard
          title="Edge Moyen"
          value={isLoading ? '...' : `${(avgEdge * 100).toFixed(1)}%`}
          subtitle="sur les picks"
          icon={TrendingUp}
          color="green"
        />
        <StatCard
          title="Win Rate"
          value="—"
          subtitle="bientot disponible"
          icon={TrendingUp}
          color="blue"
        />
        <StatCard
          title="ROI Mensuel"
          value="—"
          subtitle="bientot disponible"
          icon={TrendingUp}
          color="purple"
        />
      </div>

      {/* Recommendations */}
      <div>
        <h2 className="text-xl font-semibold text-white mb-4">
          Picks Value
        </h2>
        {isLoading ? (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {[1, 2].map((i) => (
              <div key={i} className="bg-gray-800 rounded-xl h-48 animate-pulse" />
            ))}
          </div>
        ) : recommendations.length > 0 ? (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {recommendations.slice(0, 4).map((rec: any) => (
              <RecommendationCard key={rec.id} recommendation={rec} />
            ))}
          </div>
        ) : (
          <div className="bg-gray-800 rounded-xl p-8 text-center">
            <AlertCircle className="w-12 h-12 text-gray-500 mx-auto mb-4" />
            <p className="text-gray-400">
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
      className="flex items-center gap-2 px-4 py-2 bg-brand-600 hover:bg-brand-700 disabled:bg-brand-800 disabled:opacity-60 text-white text-sm font-medium rounded-lg transition-colors"
    >
      <Download className="w-4 h-4" />
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
}

function StatCard({ title, value, subtitle, icon: Icon, color }: StatCardProps) {
  const colorClasses = {
    brand: 'bg-brand-500/10 text-brand-500',
    green: 'bg-green-500/10 text-green-500',
    blue: 'bg-blue-500/10 text-blue-500',
    purple: 'bg-purple-500/10 text-purple-500',
  }

  return (
    <div className="bg-gray-800 rounded-xl p-6">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-sm text-gray-400">{title}</p>
          <p className="text-2xl font-bold text-white mt-1">{value}</p>
          <p className="text-xs text-gray-500 mt-1">{subtitle}</p>
        </div>
        <div className={`p-3 rounded-lg ${colorClasses[color]}`}>
          <Icon className="w-6 h-6" />
        </div>
      </div>
    </div>
  )
}
