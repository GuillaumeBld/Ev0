'use client'

import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import { RefreshCw, Search, ChevronDown, ChevronUp, User, AlertCircle } from 'lucide-react'
import { clsx } from 'clsx'
import { PlayerSummary, BzzTeam, WCNation, WCSquad } from '@/lib/api'
import { getTeamId } from '@/lib/teamLogos'
import { WC2026View } from '@/components/players/WC2026View'
import { WC2026TableView } from '@/components/players/WC2026TableView'

type SortField =
  | 'name' | 'team' | 'goals' | 'goal_assist' | 'xg_per_90' | 'xa_per_90'
  | 'avg_rating' | 'shots_on_target_per_90' | 'form_xg_5' | 'minutes_played'
type PositionFilter = '' | 'G' | 'D' | 'M' | 'F'

// Recherche insensible aux accents : une lettre simple englobe TOUTES ses
// variantes. NFD + suppression des diacritiques combinants couvre é/à/ô/ü/ñ/ç/š…
// Les lettres « atomiques » (non décomposables par NFD) sont mappées à la main :
// ø (Ødegaard, Bodø), ł, đ, ð, þ, ß, æ, œ, ı (i sans point turc), etc.
const NON_DECOMPOSABLE: Record<string, string> = {
  ø: 'o', œ: 'oe', æ: 'ae', ł: 'l', đ: 'd', ð: 'd', þ: 'th',
  ß: 'ss', ı: 'i', ħ: 'h', ĸ: 'k', ŋ: 'n',
}
const foldAccents = (s: string): string =>
  s
    .normalize('NFD')
    .replace(/\p{Diacritic}/gu, '')
    .toLowerCase()
    .replace(/[øœæłđðþßıħĸŋ]/g, (c) => NON_DECOMPOSABLE[c] ?? c)

// Championnats cibles — IDs internes Bzzoiro (post-migration API)
// api_id: null = Tous, api_id: -1 = Autres (ligue dominante hors Big5/UCL)
const LEAGUES: { api_id: number | null; label: string; flag: string; finished?: boolean }[] = [
  { api_id: null, label: 'Tous', flag: '' },
  { api_id: 1,  label: 'Premier League', flag: '🏴󠁧󠁢󠁥󠁮󠁧󠁿' },
  { api_id: 6,  label: 'Ligue 1', flag: '🇫🇷', finished: true },
  { api_id: 5,  label: 'Bundesliga', flag: '🇩🇪', finished: true },
  { api_id: 3,  label: 'La Liga', flag: '🇪🇸' },
  { api_id: 4,  label: 'Serie A', flag: '🇮🇹' },
  { api_id: 7,  label: 'UCL', flag: '🏆', finished: true },
  { api_id: -1, label: 'Autres', flag: '🌍' },
]

// Backend-sortable fields (server-side sort)
const SERVER_SORT_FIELDS = new Set([
  'goals', 'goal_assist', 'xg_per_90', 'xa_per_90', 'avg_rating',
  'shots_on_target_per_90', 'form_xg_5', 'minutes_played',
])

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

export default function PlayersPage() {
  const router = useRouter()
  const searchParams = useSearchParams()

  const [players, setPlayers] = useState<PlayerSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [fetchError, setFetchError] = useState<string | null>(null)

  // Championship filter — init from URL
  const [leagueApiId, setLeagueApiId] = useState<number | null>(() => {
    const v = searchParams.get('league'); return v ? Number(v) : null
  })

  // Team filter — init from URL
  const [teams, setTeams] = useState<BzzTeam[]>([])
  const [teamApiId, setTeamApiId] = useState<number | null>(() => {
    const v = searchParams.get('team'); return v ? Number(v) : null
  })

  // Other filters — init from URL
  const [search, setSearch] = useState(() => searchParams.get('search') ?? '')
  const [positionFilter, setPositionFilter] = useState<PositionFilter>(() =>
    (searchParams.get('position') as PositionFilter) ?? ''
  )

  // Sort — init from URL
  const [sortField, setSortField] = useState<SortField>(() =>
    (searchParams.get('sort_by') as SortField) ?? 'xg_per_90'
  )
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>(() =>
    searchParams.get('sort_order') === 'asc' ? 'asc' : 'desc'
  )

  const [mode, setMode] = useState<'leagues' | 'cdm2026'>('leagues')
  const [wcNations, setWcNations] = useState<WCNation[]>([])
  const [wcSelectedNation, setWcSelectedNation] = useState<string | null>(null)
  const [wcSquad, setWcSquad] = useState<WCSquad | null>(null)
  const [wcLoading, setWcLoading] = useState(false)
  const [wcCardMode, setWcCardMode] = useState(false)

  // Keep URL in sync with filter state so browser back restores exactly this view
  useEffect(() => {
    const params = new URLSearchParams()
    if (leagueApiId !== null) params.set('league', leagueApiId.toString())
    if (teamApiId !== null) params.set('team', teamApiId.toString())
    if (search) params.set('search', search)
    if (positionFilter) params.set('position', positionFilter)
    if (sortField !== 'xg_per_90') params.set('sort_by', sortField)
    if (sortDir !== 'desc') params.set('sort_order', sortDir)
    const qs = params.toString()
    router.replace(`/dashboard/players${qs ? `?${qs}` : ''}`, { scroll: false })
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [leagueApiId, teamApiId, search, positionFilter, sortField, sortDir])

  const fetchTeams = useCallback(async (leagueId: number | null) => {
    try {
      const params = new URLSearchParams({ season: '2025-2026' })
      if (leagueId !== null) params.set('league_api_id', leagueId.toString())
      const res = await fetch(`/api/v1/players/teams?${params}`)
      if (res.ok) {
        const data: BzzTeam[] = await res.json()
        setTeams(data)
      }
    } catch {
      setTeams([])
    }
  }, [])

  const fetchPlayers = useCallback(async () => {
    setLoading(true)
    setFetchError(null)
    try {
      const params = new URLSearchParams({ season: '2025-2026', limit: '500' })
      if (leagueApiId !== null) params.set('league_api_id', leagueApiId.toString())
      if (teamApiId !== null) params.set('team_api_id', teamApiId.toString())
      if (positionFilter) params.set('position', positionFilter)
      if (SERVER_SORT_FIELDS.has(sortField)) {
        params.set('sort_by', sortField)
        params.set('sort_order', sortDir)
      } else {
        params.set('sort_by', 'xg_per_90')
        params.set('sort_order', 'desc')
      }
      const res = await fetch(`/api/v1/players?${params}`)
      if (res.ok) {
        setPlayers(await res.json())
      } else {
        setFetchError(`HTTP ${res.status}`)
      }
    } catch (err: unknown) {
      setFetchError(`Fetch error: ${err instanceof Error ? err.message : String(err)}`)
    } finally {
      setLoading(false)
    }
  }, [leagueApiId, teamApiId, positionFilter, sortField, sortDir])

  const fetchWcNations = useCallback(async () => {
    try {
      const res = await fetch('/api/v1/wc2026/nations')
      if (res.ok) setWcNations(await res.json())
    } catch { /* ignore */ }
  }, [])

  const fetchWcSquad = useCallback(async (nation: string) => {
    setWcLoading(true)
    setWcSquad(null)
    try {
      const res = await fetch(`/api/v1/wc2026/squads?nation=${encodeURIComponent(nation)}`)
      if (res.ok) setWcSquad(await res.json())
    } finally {
      setWcLoading(false)
    }
  }, [])

  useEffect(() => {
    if (mode === 'cdm2026' && wcNations.length === 0) fetchWcNations()
  }, [mode, wcNations.length, fetchWcNations])

  // Auto-select first nation once nations are loaded
  useEffect(() => {
    if (wcNations.length > 0 && !wcSelectedNation) {
      setWcSelectedNation(wcNations[0].nation)
    }
  }, [wcNations, wcSelectedNation])

  useEffect(() => {
    if (wcSelectedNation) fetchWcSquad(wcSelectedNation)
  }, [wcSelectedNation, fetchWcSquad])

  // On league change: reset team filter (but not on first mount — URL may already have a team)
  const isMounted = useRef(false)
  useEffect(() => {
    if (!isMounted.current) {
      isMounted.current = true
      fetchTeams(leagueApiId)
      return
    }
    setTeamApiId(null)
    fetchTeams(leagueApiId)
  }, [leagueApiId, fetchTeams])

  useEffect(() => {
    fetchPlayers()
  }, [fetchPlayers])

  const handleSort = (field: SortField) => {
    if (sortField === field) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortField(field)
      setSortDir('desc')
    }
  }

  // Client-side sort for name/team (not in backend sort), filter by search
  const displayed = useMemo(() => {
    let result = [...players]

    if (search.trim()) {
      const q = foldAccents(search)
      result = result.filter(
        (p) =>
          foldAccents(p.name).includes(q) ||
          (p.short_name ? foldAccents(p.short_name).includes(q) : false) ||
          (p.team_name ? foldAccents(p.team_name).includes(q) : false)
      )
    }

    if (!SERVER_SORT_FIELDS.has(sortField)) {
      result.sort((a, b) => {
        const aVal = sortField === 'name' ? a.name : (a.team_name ?? '')
        const bVal = sortField === 'name' ? b.name : (b.team_name ?? '')
        return sortDir === 'asc' ? aVal.localeCompare(bVal) : bVal.localeCompare(aVal)
      })
    }

    return result
  }, [players, search, sortField, sortDir])

  const SortIcon = ({ field }: { field: SortField }) => {
    if (sortField !== field) return <ChevronDown className="w-3 h-3 text-gray-600" />
    return sortDir === 'asc'
      ? <ChevronUp className="w-4 h-4 text-brand-400" />
      : <ChevronDown className="w-4 h-4 text-brand-400" />
  }

  const SortTh = ({ field, label, className }: { field: SortField; label: string; className?: string }) => (
    <th
      className={clsx('px-3 py-3 text-sm font-medium text-gray-400 cursor-pointer hover:text-white select-none whitespace-nowrap', className)}
      onClick={() => handleSort(field)}
    >
      <div className={clsx('flex items-center gap-1', className?.includes('text-right') ? 'justify-end' : '')}>
        {label} <SortIcon field={field} />
      </div>
    </th>
  )

  const positions: PositionFilter[] = ['', 'G', 'D', 'M', 'F']
  const positionLabels: Record<PositionFilter, string> = { '': 'Tous', G: 'G', D: 'D', M: 'M', F: 'F' }

  return (
    <div className="p-4 md:p-8">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-6">
        <div>
          <h1 className="text-2xl font-bold text-white">Joueurs</h1>
          <p className="text-gray-400 mt-1 text-sm">{players.length} joueurs · Bzzoiro · 2025-2026</p>
        </div>
        <button
          onClick={fetchPlayers}
          disabled={loading}
          className={clsx(
            'flex items-center gap-2 px-4 py-2 rounded-lg transition-colors text-sm',
            loading ? 'bg-gray-700 text-gray-400 cursor-not-allowed' : 'bg-brand-600 hover:bg-brand-700 text-white'
          )}
        >
          <RefreshCw className={clsx('w-4 h-4', loading && 'animate-spin')} />
          {loading ? 'Chargement...' : 'Actualiser'}
        </button>
      </div>

      {/* Ligne 1 — Championnats */}
      <div className="flex flex-wrap gap-2 mb-3">
        {LEAGUES.map(({ api_id, label, flag, finished }) => (
          <button
            key={api_id ?? 'all'}
            onClick={() => setLeagueApiId(api_id)}
            className={clsx(
              'px-3 py-1.5 rounded-full text-sm font-medium transition-colors border flex items-center gap-1.5',
              leagueApiId === api_id
                ? 'bg-brand-600 border-brand-600 text-white'
                : 'bg-gray-800 border-gray-700 text-gray-400 hover:border-gray-500 hover:text-white'
            )}
          >
            {flag && <span>{flag}</span>}
            {label}
            {finished && (
              <span className="text-[10px] font-semibold px-1 py-0.5 rounded bg-gray-600/60 text-gray-400 leading-none">
                FIN
              </span>
            )}
          </button>
        ))}
        <button
          onClick={() => { setMode('cdm2026'); setWcCardMode(false) }}
          className={clsx(
            'px-3 py-1.5 rounded-full text-sm font-medium transition-colors border flex items-center gap-1.5',
            mode === 'cdm2026'
              ? 'bg-amber-600 border-amber-600 text-white'
              : 'bg-gray-800 border-gray-700 text-gray-400 hover:border-gray-500 hover:text-white'
          )}
        >
          🌍 CDM 2026
        </button>
      </div>

      {mode === 'cdm2026' ? (
        wcCardMode ? (
          <WC2026View
            nations={wcNations}
            selectedNation={wcSelectedNation}
            squad={wcSquad}
            loading={wcLoading}
            onSelectNation={(n) => setWcSelectedNation(n)}
            onSwitchToTable={() => setWcCardMode(false)}
          />
        ) : (
          <WC2026TableView
            onSwitchToCards={(nation) => {
              setWcCardMode(true)
              if (nation) setWcSelectedNation(nation)
            }}
          />
        )
      ) : (
        <>
          {/* Ligne 2 — Recherche, équipes, position */}
          <div className="flex flex-wrap gap-3 mb-5 items-center">
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

            <select
              value={teamApiId ?? ''}
              onChange={(e) => setTeamApiId(e.target.value ? Number(e.target.value) : null)}
              className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:ring-2 focus:ring-brand-500 min-w-[160px]"
            >
              <option value="">🏟️ Toutes les équipes</option>
              {teams.map((t) => (
                <option key={t.api_id} value={t.api_id}>{t.name}</option>
              ))}
            </select>

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
          </div>

          {fetchError && (
            <div className="bg-red-900/50 border border-red-500 rounded-lg p-4 mb-4 flex items-center gap-2">
              <AlertCircle className="w-4 h-4 text-red-400 shrink-0" />
              <p className="text-red-300 text-sm font-mono">{fetchError}</p>
            </div>
          )}

          {loading ? (
            <div className="flex items-center justify-center py-20">
              <RefreshCw className="w-8 h-8 text-brand-500 animate-spin" />
            </div>
          ) : displayed.length === 0 ? (
            <div className="bg-gray-800 rounded-xl p-12 text-center">
              <User className="w-12 h-12 text-gray-600 mx-auto mb-4" />
              <h3 className="text-lg font-medium text-white mb-2">Aucun joueur</h3>
              <p className="text-gray-400 text-sm">
                {players.length === 0
                  ? 'Les données Bzzoiro ne sont pas encore disponibles.'
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
                      <SortTh field="goals" label="Buts" className="text-right hidden sm:table-cell" />
                      <SortTh field="goal_assist" label="PD" className="text-right hidden sm:table-cell" />
                      <SortTh field="xg_per_90" label="xG/90" className="text-right hidden sm:table-cell" />
                      <SortTh field="xa_per_90" label="xA/90" className="text-right hidden sm:table-cell" />
                      <SortTh field="avg_rating" label="Rating" className="text-right hidden md:table-cell" />
                      <SortTh field="shots_on_target_per_90" label="SoT/90" className="text-right hidden md:table-cell" />
                      <SortTh field="form_xg_5" label="Forme" className="text-right hidden lg:table-cell" />
                      <SortTh field="minutes_played" label="Mins" className="text-right" />
                    </tr>
                  </thead>
                  <tbody>
                    {displayed.map((player) => (
                      <tr
                        key={player.player_api_id}
                        className="border-b border-gray-700/50 hover:bg-gray-700/40 cursor-pointer transition-colors"
                        onClick={() => router.push(`/dashboard/players/${player.player_api_id}`)}
                      >
                        <td className="px-4 py-3">
                          <p className="text-sm font-medium text-white leading-tight">{player.name}</p>
                          {player.nationality && <p className="text-xs text-gray-500">{player.nationality}</p>}
                        </td>
                        <td className="px-3 py-3">
                          <div className="flex items-center gap-1.5 max-w-[140px]">
                            {(() => {
                              const lid = player.team_name ? getTeamId(player.team_name) : null
                              return lid ? (
                                <img
                                  src={`https://media.api-sports.io/football/teams/${lid}.png`}
                                  alt=""
                                  className="w-5 h-5 object-contain shrink-0"
                                  onError={(e) => { e.currentTarget.style.display = 'none' }}
                                />
                              ) : null
                            })()}
                            <span className="text-sm text-gray-300 truncate">{player.team_name ?? '—'}</span>
                          </div>
                        </td>
                        <td className="px-3 py-3 text-center hidden md:table-cell">
                          <span className={clsx('px-2 py-0.5 rounded text-xs font-medium', positionColor(player.position))}>
                            {player.position ?? '?'}
                          </span>
                        </td>
                        <td className="px-3 py-3 text-right hidden sm:table-cell">
                          <span className="text-sm font-mono text-white font-semibold">{player.goals ?? '—'}</span>
                        </td>
                        <td className="px-3 py-3 text-right hidden sm:table-cell">
                          <span className="text-sm font-mono text-gray-300 font-semibold">{player.goal_assist ?? '—'}</span>
                        </td>
                        <td className="px-3 py-3 text-right hidden sm:table-cell">
                          <span className="text-sm font-mono text-green-400 font-medium">{fmt(player.xg_per_90)}</span>
                        </td>
                        <td className="px-3 py-3 text-right hidden sm:table-cell">
                          <span className="text-sm font-mono text-blue-400">{fmt(player.xa_per_90)}</span>
                        </td>
                        <td className="px-3 py-3 text-right hidden md:table-cell">
                          <span className={clsx('text-sm font-mono',
                            player.avg_rating != null
                              ? player.avg_rating >= 7.5 ? 'text-amber-400 font-semibold'
                              : player.avg_rating >= 7.0 ? 'text-yellow-400'
                              : 'text-gray-300'
                              : 'text-gray-600'
                          )}>
                            {fmt(player.avg_rating, 1)}
                          </span>
                        </td>
                        <td className="px-3 py-3 text-right hidden md:table-cell">
                          <span className="text-sm font-mono text-gray-300">{fmt(player.shots_on_target_per_90)}</span>
                        </td>
                        <td className="px-3 py-3 text-right hidden lg:table-cell">
                          <span className="text-sm font-mono text-gray-400">{fmt(player.form_xg_5)}</span>
                        </td>
                        <td className="px-3 py-3 text-right">
                          <span className="text-sm font-mono text-gray-400">{player.minutes_played ?? 0}</span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          <div className="mt-4 text-sm text-gray-500 text-right">
            Affichage : {displayed.length} / {players.length} joueurs
          </div>
        </>
      )}
    </div>
  )
}
