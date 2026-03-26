'use client'

import { useState, useEffect, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Filter, RefreshCw, TrendingUp, Calendar, ChevronDown } from 'lucide-react'
import { RecommendationCard } from '@/components/RecommendationCard'
import { getRecommendations, getExpiredRecommendations } from '@/lib/api'
import { LineupData } from '@/components/lineups/LineupDisplay'

type MarketFilter = 'all' | 'goalscorer' | 'assist'
type EdgeFilter = 'all' | '5+' | '10+' | '15+'

type FixtureLineupCache = {
  home_team: string
  away_team: string
  home: LineupData | null
  away: LineupData | null
}

function edgeFilterToMinEdge(f: EdgeFilter): number {
  if (f === '5+') return 0.05
  if (f === '10+') return 0.10
  if (f === '15+') return 0.15
  return 0 // 'all' → envoie min_edge=0 pour ne pas utiliser le défaut 0.05 du backend
}

function parseOpponent(fixtureName: string, team: string): string {
  const parts = fixtureName.split(' vs ')
  if (parts.length === 2) {
    return parts[0].trim() === team ? parts[1].trim() : parts[0].trim()
  }
  return fixtureName
}

interface ApiRecommendation {
  id: number
  fixture_id: string
  fixture_name: string
  kickoff_utc: string
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
  status?: string
}

export default function RecommendationsPage() {
  const [marketFilter, setMarketFilter] = useState<MarketFilter>('all')
  const [edgeFilter, setEdgeFilter] = useState<EdgeFilter>('5+')
  const [selectedDate, setSelectedDate] = useState<string>('')
  const [expiredOpen, setExpiredOpen] = useState(false)
  const [lineupCache, setLineupCache] = useState<Record<string, FixtureLineupCache>>({})
  const fetchingFixtures = useRef<Set<string>>(new Set())

  useEffect(() => {
    setSelectedDate(new Date().toISOString().split('T')[0])
  }, [])

  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['recommendations', selectedDate, marketFilter, edgeFilter],
    enabled: !!selectedDate,
    refetchInterval: 10_000,
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
        fixtureId: String(rec.fixture_id),
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
        status: (rec.status as 'pending' | 'approved' | 'rejected') ?? 'pending',
      }))
    },
  })

  const { data: expiredData } = useQuery({
    queryKey: ['recommendations-expired', selectedDate],
    enabled: !!selectedDate,
    queryFn: async () => {
      const response = await getExpiredRecommendations({ date: selectedDate })
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
  const expiredRecs = expiredData || []

  // Fetch lineups for each unique fixture in recommendations (non-fatal, cached)
  useEffect(() => {
    if (!filteredRecs.length) return
    const uniqueIds = [...new Set(filteredRecs.map((r) => r.fixtureId))]
    for (const fxId of uniqueIds) {
      if (!fxId || lineupCache[fxId] || fetchingFixtures.current.has(fxId)) continue
      fetchingFixtures.current.add(fxId)
      fetch(`/api/v1/lineups/fixture/${fxId}`)
        .then((r) => (r.ok ? r.json() : null))
        .then((d) => { if (d) setLineupCache((prev) => ({ ...prev, [fxId]: d })) })
        .catch(() => { /* non-fatal */ })
        .finally(() => fetchingFixtures.current.delete(fxId))
    }
  }, [filteredRecs]) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="p-4 md:p-8">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-8">
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

      {/* Error Banner */}
      {isError && (
        <div className="mb-6 bg-red-500/10 border border-red-500/30 rounded-lg p-4 flex items-center justify-between">
          <p className="text-sm text-red-400">
            Erreur lors du chargement des recommandations.{' '}
            {error instanceof Error ? error.message : ''}
          </p>
          <button
            onClick={() => refetch()}
            className="text-sm text-red-300 hover:text-white underline"
          >
            Réessayer
          </button>
        </div>
      )}

      {/* Recommendations Grid */}
      {isLoading ? (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className="bg-gray-800 rounded-xl h-48 animate-pulse" />
          ))}
        </div>
      ) : filteredRecs.length > 0 ? (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {filteredRecs.map((rec) => {
            const { fixtureId, ...recProps } = rec
            const fx = lineupCache[fixtureId]
            const lineup = fx
              ? (rec.team === fx.home_team ? fx.home : fx.away)
              : undefined
            return (
              <RecommendationCard key={rec.id} recommendation={{ ...recProps, lineup }} />
            )
          })}
        </div>
      ) : (
        <div className="bg-gray-800 rounded-xl p-12 text-center">
          <Filter className="w-12 h-12 text-gray-600 mx-auto mb-4" />
          <p className="text-gray-400">Aucune recommandation ne correspond aux filtres</p>
        </div>
      )}

      {/* Section Expirées */}
      <div className="mt-8">
        <button
          onClick={() => setExpiredOpen(!expiredOpen)}
          className="flex items-center gap-2 text-gray-400 hover:text-white transition-colors mb-4"
        >
          <ChevronDown className={`w-4 h-4 transition-transform ${expiredOpen ? 'rotate-180' : ''}`} />
          <span className="text-sm font-medium">Expirées ({expiredRecs.length})</span>
        </button>

        {expiredOpen && (
          expiredRecs.length === 0 ? (
            <p className="text-sm text-gray-500 italic">Aucune recommandation expirée pour cette date.</p>
          ) : (
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 opacity-50">
              {expiredRecs.map((rec) => (
                <div key={rec.id} className="relative">
                  <div className="absolute top-3 right-3 z-10 px-2 py-0.5 bg-gray-600 text-gray-300 text-xs rounded">
                    Expiré
                  </div>
                  <RecommendationCard recommendation={rec} />
                </div>
              ))}
            </div>
          )
        )}
      </div>
    </div>
  )
}
