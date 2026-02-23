'use client'

import { useState, useEffect, useCallback } from 'react'
import { RefreshCw, Search, Filter, ChevronDown, ChevronUp, Database, TrendingUp, AlertCircle } from 'lucide-react'
import { clsx } from 'clsx'

interface PlayerStats {
  source: string
  minutes: number
  goals: number
  assists: number
  xg: number
  xa: number
  npxg: number
  xg_per_90: number | null
  xa_per_90: number | null
  npxg_per_90: number | null
  shots: number
  key_passes: number
  as_of: string | null
}

interface Player {
  id: number
  name: string
  team: string | null
  position: string | null
  league: string | null
  fotmob: PlayerStats | null
  understat: PlayerStats | null
  average: PlayerStats | null
  ev0_xg_per_90: number
  ev0_xa_per_90: number
  ev0_npxg_per_90: number
}

interface SyncStatus {
  ligue_1_players: number
  ligue_1_teams: number
  premier_league_players: number
  premier_league_teams: number
  last_sync: string | null
}

type SortField = 'name' | 'team' | 'xg_per_90' | 'xa_per_90' | 'minutes'
type SourceFilter = 'average' | 'fotmob' | 'understat'

export default function PlayersPage() {
  const [players, setPlayers] = useState<Player[]>([])
  const [loading, setLoading] = useState(true)
  const [syncing, setSyncing] = useState(false)
  const [syncStatus, setSyncStatus] = useState<SyncStatus | null>(null)
  const [search, setSearch] = useState('')
  const [leagueFilter, setLeagueFilter] = useState<string>('')
  const [sourceView, setSourceView] = useState<SourceFilter>('average')
  const [sortField, setSortField] = useState<SortField>('xg_per_90')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')
  const [teamFilter, setTeamFilter] = useState<string>('')
  const [teams, setTeams] = useState<string[]>([])
  const [minMinutes, setMinMinutes] = useState(0)
  const [expandedPlayer, setExpandedPlayer] = useState<number | null>(null)
  const [fetchError, setFetchError] = useState<string | null>(null)

  const fetchTeams = useCallback(async (league: string) => {
    try {
      const params = new URLSearchParams({ limit: '100' })
      if (league) params.set('league', league)
      const res = await fetch(`/api/v1/players/teams?${params}`)
      if (res.ok) {
        const data: { name: string; league: string }[] = await res.json()
        setTeams(data.filter(t => !t.name.includes(',')).map(t => t.name))
      }
    } catch { /* ignore */ }
  }, [])

  useEffect(() => {
    setTeamFilter('')
    fetchTeams(leagueFilter)
  }, [leagueFilter, fetchTeams])

  const fetchPlayers = useCallback(async () => {
    setLoading(true)
    setFetchError(null)
    try {
      const params = new URLSearchParams()
      if (leagueFilter) params.set('league', leagueFilter)
      if (teamFilter) params.set('team', teamFilter)
      if (search) params.set('search', search)
      params.set('min_minutes', minMinutes.toString())
      params.set('limit', '500')

      const url = `/api/v1/players?${params}`
      const res = await fetch(url)
      if (res.ok) {
        const data = await res.json()
        setPlayers(data)
        setFetchError(null)
      } else {
        const text = await res.text()
        setFetchError(`HTTP ${res.status}: ${text.slice(0, 200)}`)
      }
    } catch (err: any) {
      console.error('Failed to fetch players:', err)
      setFetchError(`Fetch error: ${err.message}`)
    } finally {
      setLoading(false)
    }
  }, [leagueFilter, teamFilter, search, minMinutes])

  const fetchSyncStatus = useCallback(async () => {
    try {
      const res = await fetch(`/api/v1/players/sync-status`)
      if (res.ok) {
        const data = await res.json()
        setSyncStatus(data)
      }
    } catch (err) {
      console.error('Failed to fetch sync status:', err)
    }
  }, [])

  useEffect(() => {
    fetchPlayers()
    fetchSyncStatus()
  }, [fetchPlayers, fetchSyncStatus])

  const triggerSync = async () => {
    setSyncing(true)
    try {
      await fetch('/api/v1/players/sync?strategy=direct', { method: 'POST' })
      const interval = setInterval(async () => {
        await fetchSyncStatus()
      }, 5000)
      setTimeout(() => {
        clearInterval(interval)
        setSyncing(false)
        fetchPlayers()
        fetchSyncStatus()
      }, 60000)
    } catch (err) {
      console.error('Sync failed:', err)
      setSyncing(false)
    }
  }

  const getStatsForSource = (player: Player, source: SourceFilter): PlayerStats | null => {
    switch (source) {
      case 'fotmob': return player.fotmob
      case 'understat': return player.understat
      case 'average': return player.average
    }
  }

  const sortedPlayers = [...players].sort((a, b) => {
    let aVal: number | string = 0
    let bVal: number | string = 0

    const aStats = getStatsForSource(a, sourceView)
    const bStats = getStatsForSource(b, sourceView)

    switch (sortField) {
      case 'name':
        aVal = a.name
        bVal = b.name
        break
      case 'team':
        aVal = a.team || ''
        bVal = b.team || ''
        break
      case 'xg_per_90':
        aVal = aStats?.xg_per_90 || 0
        bVal = bStats?.xg_per_90 || 0
        break
      case 'xa_per_90':
        aVal = aStats?.xa_per_90 || 0
        bVal = bStats?.xa_per_90 || 0
        break
      case 'minutes':
        aVal = aStats?.minutes || 0
        bVal = bStats?.minutes || 0
        break
    }

    if (typeof aVal === 'string') {
      return sortDir === 'asc' ? aVal.localeCompare(bVal as string) : (bVal as string).localeCompare(aVal)
    }
    return sortDir === 'asc' ? aVal - (bVal as number) : (bVal as number) - aVal
  })

  const handleSort = (field: SortField) => {
    if (sortField === field) {
      setSortDir(sortDir === 'asc' ? 'desc' : 'asc')
    } else {
      setSortField(field)
      setSortDir('desc')
    }
  }

  const SortIcon = ({ field }: { field: SortField }) => {
    if (sortField !== field) return null
    return sortDir === 'asc' ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />
  }

  const formatDiff = (fotmob: number | null, understat: number | null): { diff: number; color: string } | null => {
    if (fotmob === null || understat === null) return null
    const diff = fotmob - understat
    const color = Math.abs(diff) < 0.05 ? 'text-gray-400' : diff > 0 ? 'text-green-400' : 'text-red-400'
    return { diff, color }
  }

  return (
    <div className="p-4 md:p-8">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-6">
        <div>
          <h1 className="text-2xl font-bold text-white">Joueurs</h1>
          <p className="text-gray-400 mt-1">
            {syncStatus && (
              <>
                L1: {syncStatus.ligue_1_players} joueurs • PL: {syncStatus.premier_league_players} joueurs
                {syncStatus.last_sync && (
                  <span className="ml-2 text-gray-500">
                    (màj: {new Date(syncStatus.last_sync).toLocaleDateString('fr-FR')})
                  </span>
                )}
              </>
            )}
          </p>
        </div>
        <button
          onClick={triggerSync}
          disabled={syncing}
          className={clsx(
            "flex items-center gap-2 px-4 py-2 rounded-lg transition-colors",
            syncing
              ? "bg-gray-700 text-gray-400 cursor-not-allowed"
              : "bg-brand-600 hover:bg-brand-700 text-white"
          )}
        >
          <RefreshCw className={clsx("w-4 h-4", syncing && "animate-spin")} />
          {syncing ? 'Sync en cours...' : 'Sync joueurs'}
        </button>
      </div>

      {/* Filters */}
      <div className="grid grid-cols-1 md:grid-cols-5 gap-4 mb-6">
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-5 h-5 text-gray-400" />
          <input
            type="text"
            placeholder="Rechercher..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full pl-10 pr-4 py-2.5 bg-gray-800 border border-gray-700 rounded-lg text-white placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-brand-500"
          />
        </div>

        <select
          value={leagueFilter}
          onChange={(e) => setLeagueFilter(e.target.value)}
          className="bg-gray-800 border border-gray-700 rounded-lg px-4 py-2.5 text-white focus:outline-none focus:ring-2 focus:ring-brand-500"
        >
          <option value="">Toutes les ligues</option>
          <option value="ligue_1">Ligue 1</option>
          <option value="premier_league">Premier League</option>
        </select>

        <select
          value={teamFilter}
          onChange={(e) => setTeamFilter(e.target.value)}
          className="bg-gray-800 border border-gray-700 rounded-lg px-4 py-2.5 text-white focus:outline-none focus:ring-2 focus:ring-brand-500"
        >
          <option value="">Toutes les équipes</option>
          {teams.map(t => (
            <option key={t} value={t}>{t}</option>
          ))}
        </select>

        <select
          value={sourceView}
          onChange={(e) => setSourceView(e.target.value as SourceFilter)}
          className="bg-gray-800 border border-gray-700 rounded-lg px-4 py-2.5 text-white focus:outline-none focus:ring-2 focus:ring-brand-500"
        >
          <option value="average">📊 Moyenne (FotMob + Understat)</option>
          <option value="fotmob">⚽ FotMob uniquement</option>
          <option value="understat">📉 Understat uniquement</option>
        </select>

        <div className="flex items-center gap-2">
          <span className="text-gray-400 text-sm whitespace-nowrap">Min. mins:</span>
          <input
            type="number"
            value={minMinutes}
            onChange={(e) => setMinMinutes(Number(e.target.value))}
            className="w-24 bg-gray-800 border border-gray-700 rounded-lg px-3 py-2.5 text-white text-center focus:outline-none focus:ring-2 focus:ring-brand-500"
            step={90}
            min={0}
          />
          <button
            onClick={() => fetchPlayers()}
            className="p-2.5 bg-gray-700 hover:bg-gray-600 rounded-lg text-white"
          >
            <Filter className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Source Legend */}
      <div className="flex items-center gap-6 mb-4 text-sm">
        <div className="flex items-center gap-2">
          <div className="w-3 h-3 rounded bg-blue-500" />
          <span className="text-gray-400">FotMob</span>
        </div>
        <div className="flex items-center gap-2">
          <div className="w-3 h-3 rounded bg-purple-500" />
          <span className="text-gray-400">Understat</span>
        </div>
        <div className="flex items-center gap-2">
          <div className="w-3 h-3 rounded bg-green-500" />
          <span className="text-gray-400">Moyenne EV0</span>
        </div>
      </div>

      {/* Debug info */}
      {fetchError && (
        <div className="bg-red-900/50 border border-red-500 rounded-lg p-4 mb-4">
          <p className="text-red-300 text-sm font-mono">{fetchError}</p>
        </div>
      )}

      {/* Table */}
      {loading ? (
        <div className="flex items-center justify-center py-20">
          <RefreshCw className="w-8 h-8 text-brand-500 animate-spin" />
        </div>
      ) : players.length === 0 ? (
        <div className="bg-gray-800 rounded-xl p-12 text-center">
          <Database className="w-12 h-12 text-gray-600 mx-auto mb-4" />
          <h3 className="text-lg font-medium text-white mb-2">Aucun joueur</h3>
          <p className="text-gray-400 mb-4">
            Lancez une synchronisation pour importer les joueurs de FotMob et Understat.
          </p>
          <button
            onClick={triggerSync}
            disabled={syncing}
            className="px-4 py-2 bg-brand-600 hover:bg-brand-700 text-white rounded-lg"
          >
            Lancer la sync
          </button>
        </div>
      ) : (
        <div className="bg-gray-800 rounded-xl overflow-hidden">
          <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="border-b border-gray-700">
                <th
                  className="px-4 py-3 text-left text-sm font-medium text-gray-400 cursor-pointer hover:text-white"
                  onClick={() => handleSort('name')}
                >
                  <div className="flex items-center gap-1">
                    Joueur <SortIcon field="name" />
                  </div>
                </th>
                <th
                  className="px-4 py-3 text-left text-sm font-medium text-gray-400 cursor-pointer hover:text-white"
                  onClick={() => handleSort('team')}
                >
                  <div className="flex items-center gap-1">
                    Équipe <SortIcon field="team" />
                  </div>
                </th>
                <th className="px-4 py-3 text-center text-sm font-medium text-gray-400 hidden md:table-cell">Pos</th>
                <th
                  className="px-4 py-3 text-right text-sm font-medium text-gray-400 cursor-pointer hover:text-white"
                  onClick={() => handleSort('minutes')}
                >
                  <div className="flex items-center justify-end gap-1">
                    Mins <SortIcon field="minutes" />
                  </div>
                </th>
                <th className="px-4 py-3 text-right text-sm font-medium text-gray-400 hidden md:table-cell">Buts</th>
                <th className="px-4 py-3 text-right text-sm font-medium text-gray-400 hidden md:table-cell">xG</th>
                <th
                  className="px-4 py-3 text-right text-sm font-medium text-gray-400 cursor-pointer hover:text-white"
                  onClick={() => handleSort('xg_per_90')}
                >
                  <div className="flex items-center justify-end gap-1">
                    xG/90 <SortIcon field="xg_per_90" />
                  </div>
                </th>
                <th className="px-4 py-3 text-right text-sm font-medium text-gray-400 hidden md:table-cell">PD</th>
                <th className="px-4 py-3 text-right text-sm font-medium text-gray-400 hidden md:table-cell">xA</th>
                <th
                  className="px-4 py-3 text-right text-sm font-medium text-gray-400 cursor-pointer hover:text-white"
                  onClick={() => handleSort('xa_per_90')}
                >
                  <div className="flex items-center justify-end gap-1">
                    xA/90 <SortIcon field="xa_per_90" />
                  </div>
                </th>
                <th className="px-4 py-3 text-center text-sm font-medium text-gray-400 hidden md:table-cell">Δ Source</th>
              </tr>
            </thead>
            <tbody>
              {sortedPlayers.map((player) => {
                const stats = getStatsForSource(player, sourceView)
                const isExpanded = expandedPlayer === player.id
                const xgDiff = formatDiff(player.fotmob?.xg_per_90 ?? null, player.understat?.xg_per_90 ?? null)
                const xaDiff = formatDiff(player.fotmob?.xa_per_90 ?? null, player.understat?.xa_per_90 ?? null)

                return (
                  <>
                    <tr
                      key={player.id}
                      className={clsx(
                        "border-b border-gray-700/50 hover:bg-gray-700/30 cursor-pointer transition-colors",
                        isExpanded && "bg-gray-700/20"
                      )}
                      onClick={() => setExpandedPlayer(isExpanded ? null : player.id)}
                    >
                      <td className="px-4 py-3 text-sm text-white font-medium">{player.name}</td>
                      <td className="px-4 py-3 text-sm text-gray-300">{player.team || '-'}</td>
                      <td className="px-4 py-3 text-center hidden md:table-cell">
                        <span className={clsx(
                          'px-2 py-0.5 rounded text-xs font-medium',
                          player.position?.includes('F') ? 'bg-red-500/20 text-red-400' :
                          player.position?.includes('M') ? 'bg-green-500/20 text-green-400' :
                          player.position?.includes('D') ? 'bg-blue-500/20 text-blue-400' :
                          'bg-yellow-500/20 text-yellow-400'
                        )}>
                          {player.position || '?'}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-sm text-right text-gray-300">{stats?.minutes || 0}</td>
                      <td className="px-4 py-3 text-sm text-right text-white font-medium hidden md:table-cell">{stats?.goals || 0}</td>
                      <td className="px-4 py-3 text-sm text-right text-gray-300 hidden md:table-cell">{(stats?.xg || 0).toFixed(1)}</td>
                      <td className="px-4 py-3 text-sm text-right text-green-400 font-medium">
                        {(stats?.xg_per_90 || 0).toFixed(2)}
                      </td>
                      <td className="px-4 py-3 text-sm text-right text-white font-medium hidden md:table-cell">{stats?.assists || 0}</td>
                      <td className="px-4 py-3 text-sm text-right text-gray-300 hidden md:table-cell">{(stats?.xa || 0).toFixed(1)}</td>
                      <td className="px-4 py-3 text-sm text-right text-blue-400 font-medium">
                        {(stats?.xa_per_90 || 0).toFixed(2)}
                      </td>
                      <td className="px-4 py-3 text-center hidden md:table-cell">
                        {player.fotmob && player.understat ? (
                          <div className="flex items-center justify-center gap-1">
                            {xgDiff && Math.abs(xgDiff.diff) >= 0.05 && (
                              <span className={clsx("text-xs", xgDiff.color)} title="Δ xG/90">
                                {xgDiff.diff > 0 ? '+' : ''}{xgDiff.diff.toFixed(2)}
                              </span>
                            )}
                            {(!xgDiff || Math.abs(xgDiff.diff) < 0.05) && (
                              <span className="text-xs text-gray-500">≈</span>
                            )}
                          </div>
                        ) : (
                          <span title="Source unique"><AlertCircle className="w-4 h-4 text-yellow-500 mx-auto" /></span>
                        )}
                      </td>
                    </tr>
                    {isExpanded && (
                      <tr className="bg-gray-900/50">
                        <td colSpan={11} className="px-4 py-4">
                          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                            {/* FotMob */}
                            <div className="bg-blue-500/10 rounded-lg p-4 border border-blue-500/30">
                              <div className="flex items-center gap-2 mb-3">
                                <div className="w-3 h-3 rounded bg-blue-500" />
                                <span className="text-sm font-medium text-blue-400">FotMob</span>
                              </div>
                              {player.fotmob ? (
                                <div className="grid grid-cols-2 gap-2 text-sm">
                                  <div><span className="text-gray-400">Mins:</span> <span className="text-white">{player.fotmob.minutes}</span></div>
                                  <div><span className="text-gray-400">Buts:</span> <span className="text-white">{player.fotmob.goals}</span></div>
                                  <div><span className="text-gray-400">xG:</span> <span className="text-white">{player.fotmob.xg.toFixed(2)}</span></div>
                                  <div><span className="text-gray-400">xG/90:</span> <span className="text-green-400 font-medium">{(player.fotmob.xg_per_90 || 0).toFixed(2)}</span></div>
                                  <div><span className="text-gray-400">PD:</span> <span className="text-white">{player.fotmob.assists}</span></div>
                                  <div><span className="text-gray-400">xA:</span> <span className="text-white">{player.fotmob.xa.toFixed(2)}</span></div>
                                  <div><span className="text-gray-400">xA/90:</span> <span className="text-blue-400 font-medium">{(player.fotmob.xa_per_90 || 0).toFixed(2)}</span></div>
                                  <div><span className="text-gray-400">Tirs:</span> <span className="text-white">{player.fotmob.shots}</span></div>
                                </div>
                              ) : (
                                <p className="text-gray-500 text-sm">Données non disponibles</p>
                              )}
                            </div>

                            {/* Understat */}
                            <div className="bg-purple-500/10 rounded-lg p-4 border border-purple-500/30">
                              <div className="flex items-center gap-2 mb-3">
                                <div className="w-3 h-3 rounded bg-purple-500" />
                                <span className="text-sm font-medium text-purple-400">Understat</span>
                              </div>
                              {player.understat ? (
                                <div className="grid grid-cols-2 gap-2 text-sm">
                                  <div><span className="text-gray-400">Mins:</span> <span className="text-white">{player.understat.minutes}</span></div>
                                  <div><span className="text-gray-400">Buts:</span> <span className="text-white">{player.understat.goals}</span></div>
                                  <div><span className="text-gray-400">xG:</span> <span className="text-white">{player.understat.xg.toFixed(2)}</span></div>
                                  <div><span className="text-gray-400">xG/90:</span> <span className="text-green-400 font-medium">{(player.understat.xg_per_90 || 0).toFixed(2)}</span></div>
                                  <div><span className="text-gray-400">PD:</span> <span className="text-white">{player.understat.assists}</span></div>
                                  <div><span className="text-gray-400">xA:</span> <span className="text-white">{player.understat.xa.toFixed(2)}</span></div>
                                  <div><span className="text-gray-400">xA/90:</span> <span className="text-blue-400 font-medium">{(player.understat.xa_per_90 || 0).toFixed(2)}</span></div>
                                  <div><span className="text-gray-400">npxG/90:</span> <span className="text-white">{(player.understat.npxg_per_90 || 0).toFixed(2)}</span></div>
                                </div>
                              ) : (
                                <p className="text-gray-500 text-sm">Données non disponibles</p>
                              )}
                            </div>

                            {/* Average / EV0 */}
                            <div className="bg-green-500/10 rounded-lg p-4 border border-green-500/30">
                              <div className="flex items-center gap-2 mb-3">
                                <div className="w-3 h-3 rounded bg-green-500" />
                                <span className="text-sm font-medium text-green-400">Moyenne EV0</span>
                                <TrendingUp className="w-4 h-4 text-green-400" />
                              </div>
                              {player.average ? (
                                <div className="grid grid-cols-2 gap-2 text-sm">
                                  <div><span className="text-gray-400">Mins:</span> <span className="text-white">{player.average.minutes}</span></div>
                                  <div><span className="text-gray-400">Buts:</span> <span className="text-white">{player.average.goals}</span></div>
                                  <div><span className="text-gray-400">xG:</span> <span className="text-white">{player.average.xg.toFixed(2)}</span></div>
                                  <div><span className="text-gray-400">xG/90:</span> <span className="text-green-400 font-bold">{player.ev0_xg_per_90.toFixed(2)}</span></div>
                                  <div><span className="text-gray-400">PD:</span> <span className="text-white">{player.average.assists}</span></div>
                                  <div><span className="text-gray-400">xA:</span> <span className="text-white">{player.average.xa.toFixed(2)}</span></div>
                                  <div><span className="text-gray-400">xA/90:</span> <span className="text-blue-400 font-bold">{player.ev0_xa_per_90.toFixed(2)}</span></div>
                                  <div><span className="text-gray-400">npxG/90:</span> <span className="text-white font-bold">{player.ev0_npxg_per_90.toFixed(2)}</span></div>
                                </div>
                              ) : (
                                <p className="text-gray-500 text-sm">En attente de données des deux sources</p>
                              )}
                            </div>
                          </div>

                          {/* Différences FotMob vs Understat */}
                          {player.fotmob && player.understat && (
                            <div className="mt-4 p-3 bg-gray-800/50 rounded-lg">
                              <span className="text-sm text-gray-400">Écart FotMob - Understat: </span>
                              <span className={clsx("text-sm font-medium", xgDiff?.color || 'text-gray-400')}>
                                xG/90 {xgDiff && xgDiff.diff > 0 ? '+' : ''}{xgDiff?.diff.toFixed(3) || '0'}
                              </span>
                              <span className="text-gray-600 mx-2">|</span>
                              <span className={clsx("text-sm font-medium", xaDiff?.color || 'text-gray-400')}>
                                xA/90 {xaDiff && xaDiff.diff > 0 ? '+' : ''}{xaDiff?.diff.toFixed(3) || '0'}
                              </span>
                            </div>
                          )}
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

      {/* Stats Summary */}
      <div className="mt-4 text-sm text-gray-500 text-right">
        Affichage: {sortedPlayers.length} joueurs | Source: {sourceView === 'average' ? 'Moyenne' : sourceView === 'fotmob' ? 'FotMob' : 'Understat'}
      </div>
    </div>
  )
}
