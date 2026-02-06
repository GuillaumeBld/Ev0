'use client'

import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Filter, RefreshCw, TrendingUp, Calendar } from 'lucide-react'
import { RecommendationCard } from '@/components/RecommendationCard'
import { api } from '@/lib/api'

type MarketFilter = 'all' | 'goalscorer' | 'assist'
type EdgeFilter = 'all' | '5+' | '10+' | '15+'

export default function RecommendationsPage() {
  const [marketFilter, setMarketFilter] = useState<MarketFilter>('all')
  const [edgeFilter, setEdgeFilter] = useState<EdgeFilter>('5+')
  const [selectedDate, setSelectedDate] = useState<string>(
    new Date().toISOString().split('T')[0]
  )

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['recommendations', selectedDate, marketFilter, edgeFilter],
    queryFn: async () => {
      // TODO: Replace with actual API call
      return mockRecommendations
    },
  })

  const filteredRecs = (data || []).filter(rec => {
    if (marketFilter !== 'all' && rec.market !== marketFilter) return false
    if (edgeFilter === '5+' && rec.edge < 0.05) return false
    if (edgeFilter === '10+' && rec.edge < 0.10) return false
    if (edgeFilter === '15+' && rec.edge < 0.15) return false
    return true
  })

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

// Mock data
const mockRecommendations = [
  {
    id: '1',
    player: 'Kylian Mbappé',
    team: 'Paris Saint-Germain',
    opponent: 'Olympique Lyon',
    market: 'goalscorer',
    fairOdds: 1.95,
    bestOdds: 2.25,
    bookmaker: 'Betclic',
    edge: 0.154,
    confidence: 0.82,
    kickoff: '2024-02-01T20:45:00Z',
  },
  {
    id: '2',
    player: 'Ousmane Dembélé',
    team: 'Paris Saint-Germain',
    opponent: 'Olympique Lyon',
    market: 'assist',
    fairOdds: 3.20,
    bestOdds: 3.75,
    bookmaker: 'Unibet',
    edge: 0.172,
    confidence: 0.75,
    kickoff: '2024-02-01T20:45:00Z',
  },
  {
    id: '3',
    player: 'Mohamed Salah',
    team: 'Liverpool',
    opponent: 'Manchester City',
    market: 'goalscorer',
    fairOdds: 2.10,
    bestOdds: 2.45,
    bookmaker: 'Betclic',
    edge: 0.167,
    confidence: 0.88,
    kickoff: '2024-02-01T17:30:00Z',
  },
  {
    id: '4',
    player: 'Kevin De Bruyne',
    team: 'Manchester City',
    opponent: 'Liverpool',
    market: 'assist',
    fairOdds: 2.80,
    bestOdds: 3.10,
    bookmaker: 'PMU',
    edge: 0.107,
    confidence: 0.79,
    kickoff: '2024-02-01T17:30:00Z',
  },
]
