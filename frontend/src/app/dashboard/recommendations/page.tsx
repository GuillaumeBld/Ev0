'use client'

import { useState, useEffect, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Filter, RefreshCw, TrendingUp, Calendar, X } from 'lucide-react'
import { RecommendationCard } from '@/components/RecommendationCard'
import { getRecommendations, getExpiredRecommendations, Recommendation as ApiRec } from '@/lib/api'
import { LineupData } from '@/components/lineups/LineupDisplay'
import { clsx } from 'clsx'

type MarketFilter = 'all' | 'goalscorer' | 'assist'
type EdgeFilter = 'all' | '5+' | '10+' | '15+'
type StatusFilter = 'pending' | 'approved' | 'rejected' | 'expired' | 'all'

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
  if (!team) return ''
  const parts = fixtureName.split(' vs ')
  if (parts.length === 2) {
    return parts[0].trim().toLowerCase() === team.toLowerCase() ? parts[1].trim() : parts[0].trim()
  }
  return fixtureName
}

function formatDateLabel(isoDate: string): string {
  const d = new Date(isoDate + 'T00:00:00')
  return d.toLocaleDateString('fr-FR', { day: 'numeric', month: 'short' })
}

const STATUS_TABS: { key: StatusFilter; label: string }[] = [
  { key: 'pending',  label: 'En attente' },
  { key: 'approved', label: 'Approuvé' },
  { key: 'rejected', label: 'Rejeté' },
  { key: 'expired',  label: 'Expiré' },
  { key: 'all',      label: 'Tous' },
]

type ApiRecommendation = ApiRec

export default function RecommendationsPage() {
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('pending')
  const [marketFilter, setMarketFilter] = useState<MarketFilter>('all')
  const [edgeFilter, setEdgeFilter] = useState<EdgeFilter>('5+')
  const [selectedDate, setSelectedDate] = useState<string | null>(null)
  const [page, setPage] = useState(1)
  const [lineupCache, setLineupCache] = useState<Record<string, FixtureLineupCache>>({})
  const fetchingFixtures = useRef<Set<string>>(new Set())

  useEffect(() => {
    setPage(1)
  }, [selectedDate, marketFilter, edgeFilter, statusFilter])

  const isViewAll = selectedDate === null
  const isExpired = statusFilter === 'expired'

  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['recommendations', statusFilter, selectedDate, marketFilter, edgeFilter, page],
    refetchInterval: 10_000,
    queryFn: async () => {
      if (isExpired) {
        const response = await getExpiredRecommendations({
          date: selectedDate ?? undefined,
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
            status: 'expired' as const,
            decided_utc: rec.decided_utc ?? null,
            xg_source: rec.xg_source,
            is_pen_taker: rec.is_pen_taker,
          })),
          total: response.total,
          pages: response.pages,
        }
      }

      const minEdge = edgeFilterToMinEdge(edgeFilter)
      const response = await getRecommendations({
        date: selectedDate ?? undefined,
        market_type: marketFilter !== 'all' ? marketFilter : undefined,
        min_edge: minEdge,
        status: statusFilter,
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
          status: (rec.status as 'pending' | 'approved' | 'rejected' | 'expired') ?? 'pending',
          decided_utc: rec.decided_utc ?? null,
          xg_source: rec.xg_source,
          is_pen_taker: rec.is_pen_taker,
        })),
        total: response.total,
        pages: response.pages,
      }
    },
  })

  const filteredRecs = data?.recs || []
  const totalPages = data?.pages || 1

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

  const countLabel = isViewAll ? (data?.total ?? 0) : filteredRecs.length

  return (
    <div className="p-4 md:p-8">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-8">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-white">Recommandations</h1>
          <p className="text-ev-t4 mt-1 text-sm">
            {countLabel} picks · {STATUS_TABS.find(t => t.key === statusFilter)?.label}
          </p>
        </div>
        <button
          onClick={() => refetch()}
          className="health-refresh-btn flex items-center gap-2 px-4 py-2 bg-transparent border border-ev-bd hover:border-ev-bd text-ev-t3 hover:text-ev-t2 rounded-lg transition-all duration-200 text-sm font-medium"
        >
          <RefreshCw className="health-refresh-icon w-3.5 h-3.5" />
          Actualiser
        </button>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-3 mb-7">
        {/* Status filter */}
        <div className="flex items-center gap-0.5 bg-ev-surface border border-ev-bd rounded-full p-0.5">
          {STATUS_TABS.map(({ key, label }) => {
            const isActive = statusFilter === key
            const activeStyle =
              key === 'approved' ? 'bg-emerald-500/[0.12] text-emerald-400' :
              key === 'rejected' ? 'bg-rose-500/[0.12] text-rose-400' :
              key === 'expired'  ? 'bg-white/[0.06] text-ev-t3' :
              key === 'all'      ? 'bg-white/[0.08] text-white' :
                                   'bg-white/[0.08] text-white'
            return (
              <button
                key={key}
                onClick={() => setStatusFilter(key)}
                className={clsx(
                  'px-3 py-1.5 rounded-full text-xs font-medium transition-all duration-150',
                  isActive ? activeStyle : 'text-ev-t4 hover:text-ev-t2'
                )}
              >
                {label}
              </button>
            )
          })}
        </div>

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
          <div className="relative flex items-center gap-2 bg-ev-surface border border-ev-bd hover:border-ev-bd rounded-full px-3 py-1.5 cursor-pointer transition-colors">
            <Calendar className="w-3.5 h-3.5 text-ev-t4" />
            <span className="text-ev-t4 text-xs">Filtrer par date</span>
            <input
              type="date"
              onChange={(e) => { if (e.target.value) setSelectedDate(e.target.value) }}
              className="absolute inset-0 opacity-0 cursor-pointer w-full"
              aria-label="Filtrer par date"
            />
          </div>
        )}

        {/* Market filter — hidden on expired tab */}
        {statusFilter !== 'expired' && (
          <div className="flex items-center gap-0.5 bg-ev-surface border border-ev-bd rounded-full p-0.5">
            {(['all', 'goalscorer', 'assist'] as const).map((m) => (
              <button
                key={m}
                onClick={() => setMarketFilter(m)}
                className={clsx(
                  'px-3 py-1.5 rounded-full text-xs font-medium transition-all duration-150',
                  marketFilter === m
                    ? 'bg-white/[0.08] text-white'
                    : 'text-ev-t4 hover:text-ev-t2'
                )}
              >
                {m === 'all' ? 'Tous' : m === 'goalscorer' ? 'Buteur' : 'Passeur'}
              </button>
            ))}
          </div>
        )}

        {/* Edge filter — only on pending / all tabs */}
        {(statusFilter === 'pending' || statusFilter === 'all') && (
          <div className="flex items-center gap-0.5 bg-ev-surface border border-ev-bd rounded-full p-0.5">
            <TrendingUp className="w-3 h-3 text-ev-t5 ml-2" />
            {(['all', '5+', '10+', '15+'] as const).map((e) => (
              <button
                key={e}
                onClick={() => setEdgeFilter(e)}
                className={clsx(
                  'px-3 py-1.5 rounded-full text-xs font-medium transition-all duration-150',
                  edgeFilter === e
                    ? 'bg-emerald-500/[0.12] text-emerald-400'
                    : 'text-ev-t4 hover:text-ev-t2'
                )}
              >
                {e === 'all' ? 'Tous' : `${e}%`}
              </button>
            ))}
          </div>
        )}
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
            <div key={i} className="bg-ev-surface border border-ev-bd2 rounded-xl h-48 animate-pulse" />
          ))}
        </div>
      ) : filteredRecs.length > 0 ? (
        <div className={clsx(
          'grid grid-cols-1 lg:grid-cols-2 gap-4 ev0-stagger',
          isExpired && 'opacity-50'
        )}>
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
        <div className="bg-ev-surface border border-ev-bd rounded-xl p-12 text-center">
          <Filter className="w-7 h-7 text-ev-t5 mx-auto mb-3" />
          <p className="text-ev-t4 text-sm">Aucune recommandation ne correspond aux filtres</p>
        </div>
      )}

      {/* Pagination */}
      {isViewAll && totalPages > 1 && (
        <div className="flex items-center justify-center gap-3 mt-6">
          <button
            aria-label="Page précédente"
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page === 1}
            className="px-3 py-1.5 bg-ev-surface border border-ev-bd text-ev-t3 rounded-lg disabled:opacity-30 hover:border-ev-bd hover:text-ev-t1 transition-all text-sm"
          >
            ←
          </button>
          <span className="text-ev-t4 text-xs font-mono">
            {page} / {totalPages}
          </span>
          <button
            aria-label="Page suivante"
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            disabled={page === totalPages}
            className="px-3 py-1.5 bg-ev-surface border border-ev-bd text-ev-t3 rounded-lg disabled:opacity-30 hover:border-ev-bd hover:text-ev-t1 transition-all text-sm"
          >
            →
          </button>
        </div>
      )}
    </div>
  )
}
