'use client'

import { useState, useEffect, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Filter, RefreshCw, TrendingUp, Calendar, ChevronDown, X } from 'lucide-react'
import { RecommendationCard } from '@/components/RecommendationCard'
import { getRecommendations, getExpiredRecommendations, Recommendation as ApiRec } from '@/lib/api'
import { LineupData } from '@/components/lineups/LineupDisplay'
import { clsx } from 'clsx'

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

type ApiRecommendation = ApiRec

export default function RecommendationsPage() {
  const [marketFilter, setMarketFilter] = useState<MarketFilter>('all')
  const [edgeFilter, setEdgeFilter] = useState<EdgeFilter>('5+')
  const [selectedDate, setSelectedDate] = useState<string | null>(null)
  const [page, setPage] = useState(1)
  const [expiredPage, setExpiredPage] = useState(1)
  const [expiredOpen, setExpiredOpen] = useState(false)
  const [lineupCache, setLineupCache] = useState<Record<string, FixtureLineupCache>>({})
  const fetchingFixtures = useRef<Set<string>>(new Set())

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
          xg_source: rec.xg_source,
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
          xg_source: rec.xg_source,
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
          <h1 className="text-2xl font-bold tracking-tight text-white">Recommandations</h1>
          <p className="text-slate-600 mt-1 text-sm">
            {isViewAll
              ? `${data?.total ?? 0} picks disponibles`
              : `${filteredRecs.length} picks disponibles`}
          </p>
        </div>
        <button
          onClick={() => refetch()}
          className="health-refresh-btn flex items-center gap-2 px-4 py-2 bg-transparent border border-white/[0.08] hover:border-white/20 text-slate-500 hover:text-slate-200 rounded-lg transition-all duration-200 text-sm font-medium"
        >
          <RefreshCw className="health-refresh-icon w-3.5 h-3.5" />
          Actualiser
        </button>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-3 mb-7">
        {/* Date filter */}
        {selectedDate ? (
          <div className="flex items-center gap-2 bg-emerald-500/[0.08] border border-emerald-500/20 rounded-full px-3 py-1.5">
            <Calendar className="w-3.5 h-3.5 text-emerald-500" />
            <span className="text-emerald-400 text-xs font-medium">{formatDateLabel(selectedDate)}</span>
            <button
              onClick={() => setSelectedDate(null)}
              className="text-emerald-600 hover:text-emerald-300 ml-0.5 transition-colors"
              aria-label="Supprimer le filtre date"
            >
              <X className="w-3 h-3" />
            </button>
          </div>
        ) : (
          <div className="relative flex items-center gap-2 bg-[#1c1e24] border border-white/[0.07] hover:border-white/15 rounded-full px-3 py-1.5 cursor-pointer transition-colors">
            <Calendar className="w-3.5 h-3.5 text-slate-600" />
            <span className="text-slate-600 text-xs">Filtrer par date</span>
            <input
              type="date"
              onChange={(e) => { if (e.target.value) setSelectedDate(e.target.value) }}
              className="absolute inset-0 opacity-0 cursor-pointer w-full"
              aria-label="Filtrer par date"
            />
          </div>
        )}

        {/* Market filter */}
        <div className="flex items-center gap-0.5 bg-[#1c1e24] border border-white/[0.06] rounded-full p-0.5">
          {(['all', 'goalscorer', 'assist'] as const).map((m) => (
            <button
              key={m}
              onClick={() => setMarketFilter(m)}
              className={clsx(
                'px-3 py-1.5 rounded-full text-xs font-medium transition-all duration-150',
                marketFilter === m
                  ? 'bg-white/[0.08] text-white'
                  : 'text-slate-600 hover:text-slate-300'
              )}
            >
              {m === 'all' ? 'Tous' : m === 'goalscorer' ? 'Buteur' : 'Passeur'}
            </button>
          ))}
        </div>

        {/* Edge filter */}
        <div className="flex items-center gap-0.5 bg-[#1c1e24] border border-white/[0.06] rounded-full p-0.5">
          <TrendingUp className="w-3 h-3 text-slate-700 ml-2" />
          {(['all', '5+', '10+', '15+'] as const).map((e) => (
            <button
              key={e}
              onClick={() => setEdgeFilter(e)}
              className={clsx(
                'px-3 py-1.5 rounded-full text-xs font-medium transition-all duration-150',
                edgeFilter === e
                  ? 'bg-emerald-500/[0.12] text-emerald-400'
                  : 'text-slate-600 hover:text-slate-300'
              )}
            >
              {e === 'all' ? 'Tous' : `${e}%`}
            </button>
          ))}
        </div>
      </div>

      {/* Error Banner */}
      {isError && (
        <div className="mb-6 bg-rose-500/[0.07] border border-rose-500/20 rounded-xl p-4 flex items-center justify-between">
          <p className="text-sm text-rose-400">
            Erreur lors du chargement des recommandations.{' '}
            {error instanceof Error ? error.message : ''}
          </p>
          <button
            onClick={() => refetch()}
            className="text-xs text-rose-400 hover:text-rose-200 underline transition-colors"
          >
            Réessayer
          </button>
        </div>
      )}

      {/* Recommendations Grid */}
      {isLoading ? (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className="bg-[#1c1e24] border border-white/[0.05] rounded-xl h-48 animate-pulse" />
          ))}
        </div>
      ) : filteredRecs.length > 0 ? (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 ev0-stagger">
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
        <div className="bg-[#1c1e24] border border-white/[0.06] rounded-xl p-12 text-center">
          <Filter className="w-7 h-7 text-slate-700 mx-auto mb-3" />
          <p className="text-slate-600 text-sm">Aucune recommandation ne correspond aux filtres</p>
        </div>
      )}

      {/* Pagination — active recs */}
      {isViewAll && totalPages > 1 && (
        <div className="flex items-center justify-center gap-3 mt-6">
          <button
            aria-label="Page précédente"
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page === 1}
            className="px-3 py-1.5 bg-[#1c1e24] border border-white/[0.07] text-slate-400 rounded-lg disabled:opacity-30 hover:border-white/15 hover:text-white transition-all text-sm"
          >
            ←
          </button>
          <span className="text-slate-600 text-xs font-mono">
            {page} / {totalPages}
          </span>
          <button
            aria-label="Page suivante"
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            disabled={page === totalPages}
            className="px-3 py-1.5 bg-[#1c1e24] border border-white/[0.07] text-slate-400 rounded-lg disabled:opacity-30 hover:border-white/15 hover:text-white transition-all text-sm"
          >
            →
          </button>
        </div>
      )}

      {/* Section Expirées */}
      <div className="mt-10">
        <button
          onClick={() => setExpiredOpen(!expiredOpen)}
          className="flex items-center gap-2 text-slate-600 hover:text-slate-400 transition-colors mb-4"
        >
          <ChevronDown className={clsx('w-3.5 h-3.5 transition-transform duration-200', expiredOpen && 'rotate-180')} />
          <span className="text-[10px] font-semibold uppercase tracking-[0.14em]">
            Expirées ({isViewAll ? (expiredData?.total ?? 0) : expiredRecs.length})
          </span>
        </button>

        {expiredOpen && (
          expiredRecs.length === 0 ? (
            <p className="text-xs text-slate-700 italic">
              {isViewAll
                ? 'Aucune recommandation expirée.'
                : 'Aucune recommandation expirée pour cette date.'}
            </p>
          ) : (
            <>
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 opacity-50">
                {expiredRecs.map((rec) => (
                  <div key={rec.id} className="relative">
                    <div className="absolute top-3 right-3 z-10 px-2 py-0.5 bg-white/[0.04] border border-white/[0.06] text-slate-500 text-[10px] font-medium rounded-full uppercase tracking-wider">
                      Expiré
                    </div>
                    <RecommendationCard recommendation={rec} />
                  </div>
                ))}
              </div>

              {isViewAll && expiredTotalPages > 1 && (
                <div className="flex items-center justify-center gap-3 mt-4">
                  <button
                    aria-label="Page précédente (expirées)"
                    onClick={() => setExpiredPage((p) => Math.max(1, p - 1))}
                    disabled={expiredPage === 1}
                    className="px-3 py-1.5 bg-[#1c1e24] border border-white/[0.07] text-slate-400 rounded-lg disabled:opacity-30 hover:border-white/15 hover:text-white transition-all text-sm"
                  >
                    ←
                  </button>
                  <span className="text-slate-600 text-xs font-mono">
                    {expiredPage} / {expiredTotalPages}
                  </span>
                  <button
                    aria-label="Page suivante (expirées)"
                    onClick={() => setExpiredPage((p) => Math.min(expiredTotalPages, p + 1))}
                    disabled={expiredPage === expiredTotalPages}
                    className="px-3 py-1.5 bg-[#1c1e24] border border-white/[0.07] text-slate-400 rounded-lg disabled:opacity-30 hover:border-white/15 hover:text-white transition-all text-sm"
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
