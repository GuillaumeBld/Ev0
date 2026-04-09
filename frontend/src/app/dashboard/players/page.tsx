'use client'

import { useState, useEffect, useCallback, useMemo } from 'react'
import { RefreshCw, Search, Filter, ChevronDown, ChevronUp, User, AlertCircle } from 'lucide-react'
import { clsx } from 'clsx'
import { PlayerSummary, PlayerDetail } from '@/lib/api'
import { PlayerMatchChart } from '@/components/PlayerMatchChart'

type SortField = 'name' | 'team' | 'xg_per_90' | 'xa_per_90' | 'avg_rating' | 'shots_on_target_per_90' | 'form_xg_5' | 'minutes_played'
type PositionFilter = '' | 'G' | 'D' | 'M' | 'F'

function fmt(v: number | null | undefined, decimals = 2): string {
  if (v === null || v === undefined) return '—'
  return v.toFixed(decimals)
}

function positionColor(pos: string | null | undefined): string {
  switch (pos) {
    case 'F': return 'bg-red-500/20 text-red-400'
    case 'M': return 'bg-green-500/20 text-green-400'
    case 'D': return 'bg-blue-500/20 text-blue-400'
    case 'G': return 'bg-yellow-500/20 text-yellow-400'
    default:   return 'bg-gray-500/20 text-gray-400'
  }
}

function FormBar({ value }: { value: number | null }) {
  if (value === null) return <span className="text-gray-600 text-xs">—</span>
  const pct = Math.min(value / 0.8, 1) * 100
  const color = value >= 0.5 ? 'bg-green-500' : value >= 0.25 ? 'bg-yellow-500' : 'bg-red-500'
  return (
    <div className="flex items-center gap-1.5">
      <div className="w-16 h-1.5 bg-gray-700 rounded-full overflow-hidden">
        <div className={clsx('h-full rounded-full', color)} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs text-gray-400 font-mono">{value.toFixed(2)}</span>
    </div>
  )
}

export default function PlayersPage() {
  const [players, setPlayers] = useState<PlayerSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [fetchError, setFetchError] = useState<string | null>(null)

  // Filters
  const [search, setSearch] = useState('')
  const [positionFilter, setPositionFilter] = useState<PositionFilter>('')
  const [minMinutes, setMinMinutes] = useState(0)
  const [minMinutesInput, setMinMinutesInput] = useState('0')

  // Sort
  const [sortField, setSortField] = useState<SortField>('xg_per_90')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')

  // Detail drill-down
  const [expandedId, setExpandedId] = useState<number | null>(null)
  const [playerDetail, setPlayerDetail] = useState<PlayerDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [detailError, setDetailError] = useState<string | null>(null)

  const fetchPlayers = useCallback(async () => {
    setLoading(true)
    setFetchError(null)
    try {
      const params = new URLSearchParams({
        season: '2025-2026',
        limit: '100',
        sort_by: 'xg_per_90',
        sort_order: 'desc',
      })
      if (minMinutes > 0) params.set('min_minutes', minMinutes.toString())

      const res = await fetch(`/api/v1/players?${params}`)
      if (res.ok) {
        const data: PlayerSummary[] = await res.json()
        setPlayers(data)
      } else {
        const text = await res.text()
        setFetchError(`HTTP ${res.status}: ${text.slice(0, 200)}`)
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err)
      setFetchError(`Fetch error: ${msg}`)
    } finally {
      setLoading(false)
    }
  }, [minMinutes])

  useEffect(() => {
    fetchPlayers()
  }, [fetchPlayers])

  const fetchDetail = useCallback(async (id: number) => {
    setDetailLoading(true)
    setDetailError(null)
    setPlayerDetail(null)
    try {
      const res = await fetch(`/api/v1/players/${id}?season=2025-2026`)
      if (res.ok) {
        const data: PlayerDetail = await res.json()
        setPlayerDetail(data)
      } else {
        setDetailError(`HTTP ${res.status}`)
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err)
      setDetailError(`Fetch error: ${msg}`)
    } finally {
      setDetailLoading(false)
    }
  }, [])

  const handleRowClick = (id: number) => {
    if (expandedId === id) {
      setExpandedId(null)
      setPlayerDetail(null)
    } else {
      setExpandedId(id)
      fetchDetail(id)
    }
  }

  const handleSort = (field: SortField) => {
    if (sortField === field) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortField(field)
      setSortDir('desc')
    }
  }

  const filteredAndSorted = useMemo(() => {
    let result = [...players]

    // Client-side filters
    if (search.trim()) {
      const q = search.toLowerCase()
      result = result.filter(
        (p) =>
          p.name.toLowerCase().includes(q) ||
          (p.short_name?.toLowerCase().includes(q) ?? false) ||
          (p.team_name?.toLowerCase().includes(q) ?? false)
      )
    }
    if (positionFilter) {
      result = result.filter((p) => p.position === positionFilter)
    }

    // Sort
    result.sort((a, b) => {
      let aVal: number | string = 0
      let bVal: number | string = 0

      switch (sortField) {
        case 'name':
          aVal = a.name
          bVal = b.name
          break
        case 'team':
          aVal = a.team_name ?? ''
          bVal = b.team_name ?? ''
          break
        case 'xg_per_90':
          aVal = a.xg_per_90 ?? -1
          bVal = b.xg_per_90 ?? -1
          break
        case 'xa_per_90':
          aVal = a.xa_per_90 ?? -1
          bVal = b.xa_per_90 ?? -1
          break
        case 'avg_rating':
          aVal = a.avg_rating ?? -1
          bVal = b.avg_rating ?? -1
          break
        case 'shots_on_target_per_90':
          aVal = a.shots_on_target_per_90 ?? -1
          bVal = b.shots_on_target_per_90 ?? -1
          break
        case 'form_xg_5':
          aVal = a.form_xg_5 ?? -1
          bVal = b.form_xg_5 ?? -1
          break
        case 'minutes_played':
          aVal = a.minutes_played ?? 0
          bVal = b.minutes_played ?? 0
          break
      }

      if (typeof aVal === 'string') {
        return sortDir === 'asc' ? aVal.localeCompare(bVal as string) : (bVal as string).localeCompare(aVal)
      }
      return sortDir === 'asc' ? (aVal as number) - (bVal as number) : (bVal as number) - (aVal as number)
    })

    return result
  }, [players, search, positionFilter, sortField, sortDir])

  const SortIcon = ({ field }: { field: SortField }) => {
    if (sortField !== field) return <ChevronDown className="w-3 h-3 text-gray-600" />
    return sortDir === 'asc' ? <ChevronUp className="w-4 h-4 text-brand-400" /> : <ChevronDown className="w-4 h-4 text-brand-400" />
  }

  const SortTh = ({
    field,
    label,
    className,
  }: {
    field: SortField
    label: string
    className?: string
  }) => (
    <th
      className={clsx(
        'px-3 py-3 text-sm font-medium text-gray-400 cursor-pointer hover:text-white select-none whitespace-nowrap',
        className
      )}
      onClick={() => handleSort(field)}
    >
      <div className={clsx('flex items-center gap-1', className?.includes('text-right') ? 'justify-end' : '')}>
        {label} <SortIcon field={field} />
      </div>
    </th>
  )

  const positions: PositionFilter[] = ['', 'G', 'D', 'M', 'F']
  const positionLabels: Record<PositionFilter, string> = {
    '': 'Tous',
    G: 'G',
    D: 'D',
    M: 'M',
    F: 'F',
  }

  return (
    <div className="p-4 md:p-8">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-6">
        <div>
          <h1 className="text-2xl font-bold text-white">Joueurs</h1>
          <p className="text-gray-400 mt-1 text-sm">
            {players.length} joueurs — Bzzoiro · 2025-2026
          </p>
        </div>
        <button
          onClick={() => fetchPlayers()}
          disabled={loading}
          className={clsx(
            'flex items-center gap-2 px-4 py-2 rounded-lg transition-colors text-sm',
            loading
              ? 'bg-gray-700 text-gray-400 cursor-not-allowed'
              : 'bg-brand-600 hover:bg-brand-700 text-white'
          )}
        >
          <RefreshCw className={clsx('w-4 h-4', loading && 'animate-spin')} />
          {loading ? 'Chargement...' : 'Actualiser'}
        </button>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-3 mb-5">
        {/* Search */}
        <div className="relative flex-1 min-w-48">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
          <input
            type="text"
            placeholder="Rechercher un joueur..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full pl-9 pr-4 py-2 bg-gray-800 border border-gray-700 rounded-lg text-white placeholder-gray-500 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
          />
        </div>

        {/* Position filter */}
        <div className="flex rounded-lg overflow-hidden border border-gray-700">
          {positions.map((pos) => (
            <button
              key={pos || 'all'}
              onClick={() => setPositionFilter(pos)}
              className={clsx(
                'px-3 py-2 text-sm font-medium transition-colors',
                positionFilter === pos
                  ? 'bg-brand-600 text-white'
                  : 'bg-gray-800 text-gray-400 hover:bg-gray-700 hover:text-white'
              )}
            >
              {positionLabels[pos]}
            </button>
          ))}
        </div>

        {/* Min minutes */}
        <div className="flex items-center gap-2">
          <span className="text-gray-400 text-sm whitespace-nowrap">Min. mins:</span>
          <input
            type="number"
            value={minMinutesInput}
            onChange={(e) => setMinMinutesInput(e.target.value)}
            onBlur={() => setMinMinutes(Number(minMinutesInput) || 0)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') setMinMinutes(Number(minMinutesInput) || 0)
            }}
            className="w-20 bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-white text-sm text-center focus:outline-none focus:ring-2 focus:ring-brand-500"
            step={90}
            min={0}
          />
          <button
            onClick={() => fetchPlayers()}
            className="p-2 bg-gray-700 hover:bg-gray-600 rounded-lg text-white transition-colors"
            title="Appliquer filtre"
          >
            <Filter className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Error */}
      {fetchError && (
        <div className="bg-red-900/50 border border-red-500 rounded-lg p-4 mb-4 flex items-center gap-2">
          <AlertCircle className="w-4 h-4 text-red-400 shrink-0" />
          <p className="text-red-300 text-sm font-mono">{fetchError}</p>
        </div>
      )}

      {/* Table */}
      {loading ? (
        <div className="flex items-center justify-center py-20">
          <RefreshCw className="w-8 h-8 text-brand-500 animate-spin" />
        </div>
      ) : filteredAndSorted.length === 0 ? (
        <div className="bg-gray-800 rounded-xl p-12 text-center">
          <User className="w-12 h-12 text-gray-600 mx-auto mb-4" />
          <h3 className="text-lg font-medium text-white mb-2">Aucun joueur</h3>
          <p className="text-gray-400 text-sm">
            {players.length === 0
              ? 'Les données Bzzoiro ne sont pas encore disponibles. Vérifiez la synchronisation.'
              : 'Aucun joueur ne correspond aux filtres.'}
          </p>
        </div>
      ) : (
        <div className="bg-gray-800 rounded-xl overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-gray-700">
                  <SortTh field="name" label="Joueur" className="text-left pl-4" />
                  <SortTh field="team" label="Équipe" className="text-left" />
                  <th className="px-3 py-3 text-center text-sm font-medium text-gray-400 hidden md:table-cell">Pos</th>
                  <SortTh field="xg_per_90" label="xG/90" className="text-right hidden sm:table-cell" />
                  <SortTh field="xa_per_90" label="xA/90" className="text-right hidden sm:table-cell" />
                  <SortTh field="avg_rating" label="Rating" className="text-right hidden md:table-cell" />
                  <SortTh field="shots_on_target_per_90" label="SoT/90" className="text-right hidden md:table-cell" />
                  <SortTh field="form_xg_5" label="Forme (5)" className="text-right hidden lg:table-cell" />
                  <SortTh field="minutes_played" label="Mins" className="text-right" />
                </tr>
              </thead>
              <tbody>
                {filteredAndSorted.map((player) => {
                  const isExpanded = expandedId === player.player_api_id
                  return (
                    <>
                      <tr
                        key={player.player_api_id}
                        className={clsx(
                          'border-b border-gray-700/50 hover:bg-gray-700/40 cursor-pointer transition-colors',
                          isExpanded && 'bg-gray-700/40'
                        )}
                        onClick={() => handleRowClick(player.player_api_id)}
                      >
                        {/* Name */}
                        <td className="px-4 py-3">
                          <div className="flex items-center gap-2">
                            {isExpanded ? (
                              <ChevronUp className="w-3.5 h-3.5 text-brand-400 shrink-0" />
                            ) : (
                              <ChevronDown className="w-3.5 h-3.5 text-gray-600 shrink-0" />
                            )}
                            <div>
                              <p className="text-sm font-medium text-white leading-tight">{player.name}</p>
                              {player.nationality && (
                                <p className="text-xs text-gray-500 leading-tight">{player.nationality}</p>
                              )}
                            </div>
                          </div>
                        </td>

                        {/* Team */}
                        <td className="px-3 py-3 text-sm text-gray-300 max-w-[120px] truncate">
                          {player.team_name ?? '—'}
                        </td>

                        {/* Position */}
                        <td className="px-3 py-3 text-center hidden md:table-cell">
                          <span className={clsx('px-2 py-0.5 rounded text-xs font-medium', positionColor(player.position))}>
                            {player.position ?? '?'}
                          </span>
                        </td>

                        {/* xG/90 */}
                        <td className="px-3 py-3 text-right hidden sm:table-cell">
                          <span className="text-sm font-mono text-green-400 font-medium">{fmt(player.xg_per_90)}</span>
                        </td>

                        {/* xA/90 */}
                        <td className="px-3 py-3 text-right hidden sm:table-cell">
                          <span className="text-sm font-mono text-blue-400">{fmt(player.xa_per_90)}</span>
                        </td>

                        {/* Rating */}
                        <td className="px-3 py-3 text-right hidden md:table-cell">
                          <span className={clsx(
                            'text-sm font-mono',
                            player.avg_rating !== null
                              ? player.avg_rating >= 7.5 ? 'text-amber-400 font-semibold'
                              : player.avg_rating >= 7.0 ? 'text-yellow-400'
                              : 'text-gray-300'
                              : 'text-gray-600'
                          )}>
                            {fmt(player.avg_rating, 1)}
                          </span>
                        </td>

                        {/* SoT/90 */}
                        <td className="px-3 py-3 text-right hidden md:table-cell">
                          <span className="text-sm font-mono text-gray-300">{fmt(player.shots_on_target_per_90)}</span>
                        </td>

                        {/* Form (last 5) */}
                        <td className="px-3 py-3 text-right hidden lg:table-cell">
                          <FormBar value={player.form_xg_5} />
                        </td>

                        {/* Minutes */}
                        <td className="px-3 py-3 text-right">
                          <span className="text-sm font-mono text-gray-400">{player.minutes_played ?? 0}</span>
                        </td>
                      </tr>

                      {/* Expanded detail row */}
                      {isExpanded && (
                        <tr key={`${player.player_api_id}-detail`} className="bg-gray-900/60">
                          <td colSpan={9} className="px-4 py-5">
                            {detailLoading ? (
                              <div className="flex items-center justify-center py-6">
                                <RefreshCw className="w-5 h-5 text-brand-500 animate-spin" />
                              </div>
                            ) : detailError ? (
                              <div className="flex items-center gap-2 text-red-400 text-sm">
                                <AlertCircle className="w-4 h-4" />
                                {detailError}
                              </div>
                            ) : playerDetail && playerDetail.player_api_id === player.player_api_id ? (
                              <div className="space-y-5">
                                {/* Player info header */}
                                <div className="flex flex-wrap gap-4 items-center">
                                  <div>
                                    <h3 className="text-base font-semibold text-white">{playerDetail.name}</h3>
                                    <p className="text-xs text-gray-400">
                                      {playerDetail.team_name ?? '?'}
                                      {playerDetail.position && <span> · {playerDetail.position}</span>}
                                      {playerDetail.nationality && <span> · {playerDetail.nationality}</span>}
                                      {playerDetail.date_of_birth && (
                                        <span> · Né {new Date(playerDetail.date_of_birth).getFullYear()}</span>
                                      )}
                                      {playerDetail.height && <span> · {playerDetail.height} cm</span>}
                                      {playerDetail.jersey_number && <span> · #{playerDetail.jersey_number}</span>}
                                    </p>
                                  </div>
                                  {playerDetail.market_value && (
                                    <div className="ml-auto">
                                      <span className="text-xs text-gray-500">Valeur marchande</span>
                                      <p className="text-sm font-semibold text-white">
                                        {playerDetail.market_value >= 1_000_000
                                          ? `${(playerDetail.market_value / 1_000_000).toFixed(1)}M €`
                                          : `${(playerDetail.market_value / 1_000).toFixed(0)}K €`}
                                      </p>
                                    </div>
                                  )}
                                </div>

                                {/* Season stats grid */}
                                {playerDetail.season_stats && (
                                  <div>
                                    <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-3">
                                      Stats saison 2025-2026
                                    </p>
                                    <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-3">
                                      {[
                                        { label: 'xG/90', value: fmt(playerDetail.season_stats.xg_per_90), color: 'text-green-400' },
                                        { label: 'xA/90', value: fmt(playerDetail.season_stats.xa_per_90), color: 'text-blue-400' },
                                        { label: 'Rating', value: fmt(playerDetail.season_stats.avg_rating, 1), color: 'text-amber-400' },
                                        { label: 'SoT/90', value: fmt(playerDetail.season_stats.shots_on_target_per_90), color: 'text-purple-400' },
                                        { label: 'Key pass/90', value: fmt(playerDetail.season_stats.key_pass_per_90), color: 'text-indigo-400' },
                                        { label: 'Buts', value: String(playerDetail.season_stats.goals ?? '—'), color: 'text-white' },
                                        { label: 'Passes D.', value: String(playerDetail.season_stats.goal_assist ?? '—'), color: 'text-white' },
                                        { label: 'Tirs', value: String(playerDetail.season_stats.total_shots ?? '—'), color: 'text-white' },
                                        { label: 'xG total', value: fmt(playerDetail.season_stats.expected_goals), color: 'text-gray-300' },
                                        { label: 'xA total', value: fmt(playerDetail.season_stats.expected_assists), color: 'text-gray-300' },
                                        { label: 'Matchs', value: String(playerDetail.season_stats.matches_played ?? '—'), color: 'text-gray-300' },
                                        { label: 'Minutes', value: String(playerDetail.season_stats.minutes_played ?? '—'), color: 'text-gray-300' },
                                      ].map(({ label, value, color }) => (
                                        <div key={label} className="bg-gray-800 rounded-lg px-3 py-2.5">
                                          <p className="text-xs text-gray-500 mb-1">{label}</p>
                                          <p className={clsx('text-sm font-semibold font-mono', color)}>{value}</p>
                                        </div>
                                      ))}
                                    </div>
                                  </div>
                                )}

                                {/* Match-by-match chart */}
                                {playerDetail.recent_matches.length > 0 && (
                                  <div>
                                    <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-3">
                                      Match par match (derniers {playerDetail.recent_matches.length} matchs)
                                    </p>
                                    <PlayerMatchChart matches={playerDetail.recent_matches} metric="xg" />
                                  </div>
                                )}

                                {/* Recent matches table */}
                                {playerDetail.recent_matches.length > 0 && (
                                  <div>
                                    <p className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-3">
                                      Détail matchs récents
                                    </p>
                                    <div className="overflow-x-auto">
                                      <table className="w-full text-xs">
                                        <thead>
                                          <tr className="border-b border-gray-700">
                                            <th className="text-left py-2 px-2 text-gray-500 font-medium">Date</th>
                                            <th className="text-left py-2 px-2 text-gray-500 font-medium">Adversaire</th>
                                            <th className="text-right py-2 px-2 text-gray-500 font-medium">Mins</th>
                                            <th className="text-right py-2 px-2 text-gray-500 font-medium">Buts</th>
                                            <th className="text-right py-2 px-2 text-gray-500 font-medium">PD</th>
                                            <th className="text-right py-2 px-2 text-gray-500 font-medium">xG</th>
                                            <th className="text-right py-2 px-2 text-gray-500 font-medium">SoT</th>
                                            <th className="text-right py-2 px-2 text-gray-500 font-medium">KP</th>
                                            <th className="text-right py-2 px-2 text-gray-500 font-medium">Rating</th>
                                          </tr>
                                        </thead>
                                        <tbody>
                                          {playerDetail.recent_matches.slice(0, 10).map((m) => (
                                            <tr
                                              key={m.event_api_id}
                                              className="border-b border-gray-700/30 hover:bg-gray-700/20 transition-colors"
                                            >
                                              <td className="py-1.5 px-2 text-gray-400 font-mono">
                                                {m.event_date
                                                  ? new Date(m.event_date).toLocaleDateString('fr-FR', { day: 'numeric', month: 'short' })
                                                  : '—'}
                                              </td>
                                              <td className="py-1.5 px-2 text-gray-300">
                                                <span className="text-gray-500">{m.is_home ? 'vs ' : '@ '}</span>
                                                {m.opponent ?? '—'}
                                              </td>
                                              <td className="py-1.5 px-2 text-right text-gray-400 font-mono">{m.minutes_played ?? '—'}</td>
                                              <td className={clsx('py-1.5 px-2 text-right font-mono font-semibold', (m.goals ?? 0) > 0 ? 'text-green-400' : 'text-gray-400')}>
                                                {m.goals ?? 0}
                                              </td>
                                              <td className={clsx('py-1.5 px-2 text-right font-mono', (m.goal_assist ?? 0) > 0 ? 'text-blue-400' : 'text-gray-400')}>
                                                {m.goal_assist ?? 0}
                                              </td>
                                              <td className="py-1.5 px-2 text-right font-mono text-green-400">
                                                {fmt(m.expected_goals)}
                                              </td>
                                              <td className="py-1.5 px-2 text-right font-mono text-gray-300">{m.shots_on_target ?? 0}</td>
                                              <td className="py-1.5 px-2 text-right font-mono text-gray-300">{m.key_pass ?? 0}</td>
                                              <td className={clsx(
                                                'py-1.5 px-2 text-right font-mono',
                                                m.rating !== null
                                                  ? m.rating >= 7.5 ? 'text-amber-400 font-semibold'
                                                  : m.rating >= 7.0 ? 'text-yellow-400'
                                                  : 'text-gray-300'
                                                  : 'text-gray-600'
                                              )}>
                                                {fmt(m.rating, 1)}
                                              </td>
                                            </tr>
                                          ))}
                                        </tbody>
                                      </table>
                                    </div>
                                  </div>
                                )}
                              </div>
                            ) : null}
                          </td>
                        </tr>
                      )}
                    </>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Footer */}
      <div className="mt-4 text-sm text-gray-500 text-right">
        Affichage: {filteredAndSorted.length} / {players.length} joueurs
      </div>
    </div>
  )
}
