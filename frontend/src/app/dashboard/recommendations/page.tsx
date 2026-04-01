'use client'

import { useState, useEffect, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Filter, RefreshCw, TrendingUp, Calendar, ChevronDown, X } from 'lucide-react'
import { RecommendationCard } from '@/components/RecommendationCard'
import { getRecommendations, getExpiredRecommendations, Recommendation as ApiRec } from '@/lib/api'
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
  return 0
}

function parseOpponent(fixtureName: string, team: string): string {
  const parts = fixtureName.split(' vs ')
  if (parts.length === 2) {
    return parts[0].trim() === team ? parts[1].trim() : parts[0].trim()
  }
  return fixtureName
}

function formatDateLabel(isoDate: string): string {
  const d = new Date(isoDate + 'T00:00:00')
  return d.toLocaleDateString('fr-FR', { day: 'numeric', month: 'short' })
}

// ApiRec.id is string from the API; RecommendationCard expects id: number — cast at mapping site.
// Re-alias for clarity in query functions below.
type ApiRecommendation = ApiRec

export default function RecommendationsPage() {
  const [marketFilter, setMarketFilter] = useState<MarketFilter>('all')
  const [edgeFilter, setEdgeFilter] = useState<EdgeFilter>('5+')
  const [selectedDate, setSelectedDate] = useState<string | null>(null)  // null = View All
  const [page, setPage] = useState(1)
  const [expiredPage, setExpiredPage] = useState(1)
  const [expiredOpen, setExpiredOpen] = useState(false)
  const [lineupCache, setLineupCache] = useState<Record<string, FixtureLineupCache>>({})
  const fetchingFixtures = useRef<Set<string>>(new Set())

  // Reset pages when filters change
  useEffect(() => {
    setPage(1)
    setExpiredPage(1)
  }, [selectedDate, marketFilter, edgeFilter])

  const isViewAll = selectedDate === null

  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['recommendations', selectedDate, marketFilter, edgeFilter, page],
    refetchInterval: 10_000,
    queryFn: async () => {
      const minEdge = edgeFilterToMinEdge(edgeFilter)
      const response = await getRecommendations({
        date: selectedDate ?? undefined,
        market_type: marketFilter !== 'all' ? marketFilter : undefined,
        min_edge: minEdge,
        ...(isViewAll ? { page, page_size: 50 } : {}),
      })

      const recs: ApiRecommendation[] = response.recommendations || []
      return {
        recs: recs.map((rec) => ({
          id: Number(rec.id),
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
        })),
        total: response.total,
        pages: response.pages,
      }
    },
  })

  const { data: expiredData } = useQuery({
    queryKey: ['recommendations-expired', selectedDate, expiredPage],
    refetchInterval: 10_000,
    queryFn: async () => {
      const response = await getExpiredRecommendations({
        date: selectedDate ?? undefined,
        ...(isViewAll ? { page: expiredPage, page_size: 50 } : {}),
      })
      const recs: ApiRecommendation[] = response.recommendations || []
      return {
        recs: recs.map((rec) => ({
          id: Number(rec.id),
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
        })),
        total: response.total,
        pages: response.pages,
      }
    },
  })

  const filteredRecs = data?.recs || []
  const totalPages = data?.pages || 1
  const expiredRecs = expiredData?.recs || []
  const expiredTotalPages = expiredData?.pages || 1

  // Fetch lineups for each unique fixture in recommendations (non-fatal, cached)
  useEffect(() => {
    if (!filteredRecs.length) return
    const uniqueIds = Array.from(new Set(filteredRecs.map((r) => r.fixtureId)))
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
            {isViewAll
              ? `${data?.total ?? 0} picks disponibles`
              : `${filteredRecs.length} picks disponibles`}
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
        {/* Date filter — opt-in toggle */}
        {selectedDate ? (
          <div className="flex items-center gap-2 bg-brand-700 rounded-lg px-4 py-2">
            <Calendar className="w-4 h-4 text-brand-200" />
            <span className="text-white text-sm">{formatDateLabel(selectedDate)}</span>
            <button
              onClick={() => setSelectedDate(null)}
              className="text-brand-200 hover:text-white ml-1"
              aria-label="Supprimer le filtre date"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        ) : (
          <div className="relative flex items-center gap-2 bg-gray-800 hover:bg-gray-700 rounded-lg px-4 py-2 cursor-pointer transition-colors">
            <Calendar className="w-4 h-4 text-gray-400" />
            <span className="text-gray-400 text-sm">Filtrer par date</span>
            <input
              type="date"
              onChange={(e) => { if (e.target.value) setSelectedDate(e.target.value) }}
              className="absolute inset-0 opacity-0 cursor-pointer w-full"
              aria-label="Filtrer par date"
            />
          </div>
        )}

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

      {/* Pagination — active recs (view all only) */}
      {isViewAll && totalPages > 1 && (
        <div className="flex items-center justify-center gap-4 mt-6">
          <button
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page === 1}
            className="px-3 py-1.5 bg-gray-800 text-white rounded-lg disabled:opacity-40 hover:bg-gray-700 transition-colors"
          >
            ←
          </button>
          <span className="text-gray-400 text-sm">
            Page {page} / {totalPages}
          </span>
          <button
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            disabled={page === totalPages}
            className="px-3 py-1.5 bg-gray-800 text-white rounded-lg disabled:opacity-40 hover:bg-gray-700 transition-colors"
          >
            →
          </button>
        </div>
      )}

      {/* Section Expirées */}
      <div className="mt-8">
        <button
          onClick={() => setExpiredOpen(!expiredOpen)}
          className="flex items-center gap-2 text-gray-400 hover:text-white transition-colors mb-4"
        >
          <ChevronDown className={`w-4 h-4 transition-transform ${expiredOpen ? 'rotate-180' : ''}`} />
          <span className="text-sm font-medium">
            Expirées ({isViewAll ? (expiredData?.total ?? 0) : expiredRecs.length})
          </span>
        </button>

        {expiredOpen && (
          expiredRecs.length === 0 ? (
            <p className="text-sm text-gray-500 italic">
              {isViewAll
                ? 'Aucune recommandation expirée.'
                : 'Aucune recommandation expirée pour cette date.'}
            </p>
          ) : (
            <>
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

              {/* Pagination — expired (view all only) */}
              {isViewAll && expiredTotalPages > 1 && (
                <div className="flex items-center justify-center gap-4 mt-4">
                  <button
                    onClick={() => setExpiredPage((p) => Math.max(1, p - 1))}
                    disabled={expiredPage === 1}
                    className="px-3 py-1.5 bg-gray-800 text-white rounded-lg disabled:opacity-40 hover:bg-gray-700 transition-colors"
                  >
                    ←
                  </button>
                  <span className="text-gray-400 text-sm">
                    Page {expiredPage} / {expiredTotalPages}
                  </span>
                  <button
                    onClick={() => setExpiredPage((p) => Math.min(expiredTotalPages, p + 1))}
                    disabled={expiredPage === expiredTotalPages}
                    className="px-3 py-1.5 bg-gray-800 text-white rounded-lg disabled:opacity-40 hover:bg-gray-700 transition-colors"
                  >
                    →
                  </button>
                </div>
              )}
            </>
          )
        )}
      </div>
    </div>
  )
}
