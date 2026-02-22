'use client'

import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Filter, RefreshCw, TrendingUp, Calendar } from 'lucide-react'
import { RecommendationCard } from '@/components/RecommendationCard'
import { getRecommendations } from '@/lib/api'

type MarketFilter = 'all' | 'goalscorer' | 'assist'
type EdgeFilter = 'all' | '5+' | '10+' | '15+'

function edgeFilterToMinEdge(f: EdgeFilter): number | undefined {
  if (f === '5+') return 0.05
  if (f === '10+') return 0.10
  if (f === '15+') return 0.15
  return undefined
}

function parseOpponent(fixtureName: string, team: string): string {
  const parts = fixtureName.split(' vs ')
  if (parts.length === 2) {
    return parts[0].trim() === team ? parts[1].trim() : parts[0].trim()
  }
  return fixtureName
}

interface ApiRecommendation {
  id: string
  fixture_id: string
  fixture_name: string
  kickoff_utc: string
  player_id: string
  player_name: string
  team: string
  market_type: 'goalscorer' | 'assist'
  fair_odds: number
  best_bookmaker: string
  best_odds: number
  edge: number
  classification: string
  confidence: number
  explanation: Record<string, any>
}

export default function RecommendationsPage() {
  const [marketFilter, setMarketFilter] = useState<MarketFilter>('all')
  const [edgeFilter, setEdgeFilter] = useState<EdgeFilter>('5+')
  const [selectedDate, setSelectedDate] = useState<string>(
    new Date().toISOString().split('T')[0]
  )

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['recommendations', selectedDate, marketFilter, edgeFilter],
    queryFn: async () => {
      const minEdge = edgeFilterToMinEdge(edgeFilter)
      const response = await getRecommendations({
        date: selectedDate,
        market_type: marketFilter !== 'all' ? marketFilter : undefined,
        min_edge: minEdge,
      })

      const recs: ApiRecommendation[] = response.recommendations || []
      return recs.map((rec) => ({
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
    },
  })

  const filteredRecs = data || []

  return (
    <div className="p-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-white">Recommandations</h1>
          <p className="text-gray-400 mt-1">
            {filteredRecs.length} picks disponibles
          </p>
        </div>
        <button
          onClick={() => refetch()}
          className="flex items-center gap-2 px-4 py-2 bg-gray-700 hover:bg-gray-600 text-white rounded-lg transition-colors"
        >
          <RefreshCw className="w-4 h-4" />
          Actualiser
        </button>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-4 mb-6">
        {/* Date picker */}
        <div className="flex items-center gap-2 bg-gray-800 rounded-lg px-4 py-2">
          <Calendar className="w-4 h-4 text-gray-400" />
          <input
            type="date"
            value={selectedDate}
            onChange={(e) => setSelectedDate(e.target.value)}
            className="bg-transparent text-white outline-none"
          />
        </div>

        {/* Market filter */}
        <div className="flex items-center gap-1 bg-gray-800 rounded-lg p-1">
          {(['all', 'goalscorer', 'assist'] as const).map((m) => (
            <button
              key={m}
              onClick={() => setMarketFilter(m)}
              className={`px-3 py-1.5 rounded-md text-sm transition-colors ${
                marketFilter === m
                  ? 'bg-brand-600 text-white'
                  : 'text-gray-400 hover:text-white'
              }`}
            >
              {m === 'all' ? 'Tous' : m === 'goalscorer' ? '🎯 Buteur' : '🅰️ Passeur'}
            </button>
          ))}
        </div>

        {/* Edge filter */}
        <div className="flex items-center gap-1 bg-gray-800 rounded-lg p-1">
          <TrendingUp className="w-4 h-4 text-gray-400 ml-2" />
          {(['all', '5+', '10+', '15+'] as const).map((e) => (
            <button
              key={e}
              onClick={() => setEdgeFilter(e)}
              className={`px-3 py-1.5 rounded-md text-sm transition-colors ${
                edgeFilter === e
                  ? 'bg-green-600 text-white'
                  : 'text-gray-400 hover:text-white'
              }`}
            >
              {e === 'all' ? 'Tous' : `${e}%`}
            </button>
          ))}
        </div>
      </div>

      {/* Recommendations Grid */}
      {isLoading ? (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className="bg-gray-800 rounded-xl h-48 animate-pulse" />
          ))}
        </div>
      ) : filteredRecs.length > 0 ? (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {filteredRecs.map((rec) => (
            <RecommendationCard key={rec.id} recommendation={rec} />
          ))}
        </div>
      ) : (
        <div className="bg-gray-800 rounded-xl p-12 text-center">
          <Filter className="w-12 h-12 text-gray-600 mx-auto mb-4" />
          <p className="text-gray-400">Aucune recommandation ne correspond aux filtres</p>
        </div>
      )}
    </div>
  )
}
