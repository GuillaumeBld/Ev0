'use client'

import { useState, useEffect, useCallback } from 'react'
import { Search } from 'lucide-react'
import { clsx } from 'clsx'
import { WCPlayer, WCPlayersPage, WCNation } from '@/lib/api'

const PAGE_SIZE = 50

type WCSortField =
  | 'goals' | 'assists' | 'xg_per90' | 'xa_per90' | 'avg_rating'
  | 'matches_played' | 'saves' | 'form_xg_5'

type WCPosition = '' | 'GK' | 'DEF' | 'MID' | 'FWD'

const POSITION_LABELS: Record<WCPosition, string> = {
  '': 'Tous', GK: 'GK', DEF: 'DEF', MID: 'MID', FWD: 'FWD',
}
const WC_POSITIONS: WCPosition[] = ['', 'GK', 'DEF', 'MID', 'FWD']

function fmtCell(v: number | null, decimals = 1): string {
  return v == null ? '—' : v.toFixed(decimals)
}

interface SortThProps {
  field: WCSortField
  label: string
  className?: string
  sortBy: WCSortField
  sortOrder: 'asc' | 'desc'
  onSort: (field: WCSortField) => void
}

function SortTh({ field, label, className, sortBy, sortOrder, onSort }: SortThProps) {
  return (
    <th
      onClick={() => onSort(field)}
      className={clsx(
        'px-3 py-3 text-sm font-medium cursor-pointer select-none whitespace-nowrap transition-colors',
        sortBy === field ? 'text-brand-400' : 'text-gray-400 hover:text-white',
        className,
      )}
    >
      <div className={clsx('flex items-center gap-1', className?.includes('text-right') ? 'justify-end' : '')}>
        {label}
        {sortBy === field && (
          <span className="text-brand-400">{sortOrder === 'desc' ? ' ↓' : ' ↑'}</span>
        )}
      </div>
    </th>
  )
}

interface Props {
  onSwitchToCards: (nation: string | null) => void
}

export function WC2026TableView({ onSwitchToCards }: Props) {
  const [nations, setNations] = useState<WCNation[]>([])
  const [nation, setNation] = useState<string>('')
  const [position, setPosition] = useState<WCPosition>('')
  const [search, setSearch] = useState<string>('')
  const [debouncedSearch, setDebouncedSearch] = useState<string>('')
  const [sortBy, setSortBy] = useState<WCSortField>('xg_per90')
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('desc')
  const [page, setPage] = useState<number>(1)
  const [data, setData] = useState<WCPlayersPage | null>(null)
  const [loading, setLoading] = useState<boolean>(false)
  const [error, setError] = useState<string | null>(null)

  // Debounce search
  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(search), 300)
    return () => clearTimeout(t)
  }, [search])

  // Reset page when filters change
  useEffect(() => { setPage(1) }, [nation, position, debouncedSearch, sortBy, sortOrder])

  // Load nations once for the dropdown
  useEffect(() => {
    fetch('/api/v1/wc2026/nations')
      .then(r => r.json())
      .then(setNations)
      .catch(console.error)
  }, [])

  const fetchPlayers = useCallback(async () => {
    setError(null)
    setLoading(true)
    try {
      const params = new URLSearchParams()
      if (nation) params.set('nation', nation)
      if (position) params.set('position', position)
      if (debouncedSearch) params.set('search', debouncedSearch)
      params.set('sort_by', sortBy)
      params.set('sort_order', sortOrder)
      params.set('page', page.toString())
      const res = await fetch(`/api/v1/wc2026/players?${params}`)
      setData(await res.json())
    } catch (e) {
      console.error(e)
      setError('Erreur de chargement des joueurs.')
    } finally {
      setLoading(false)
    }
  }, [nation, position, debouncedSearch, sortBy, sortOrder, page])

  useEffect(() => { fetchPlayers() }, [fetchPlayers])

  const handleSort = (field: WCSortField) => {
    if (sortBy === field) {
      setSortOrder(o => o === 'desc' ? 'asc' : 'desc')
    } else {
      setSortBy(field)
      setSortOrder('desc')
    }
  }

  const totalPages = data ? Math.ceil(data.total / PAGE_SIZE) : 0

  return (
    <div>
      {/* Filters */}
      <div className="flex flex-wrap gap-3 mb-5 items-center">
        <select
          value={nation}
          onChange={e => setNation(e.target.value)}
          className="bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:ring-2 focus:ring-brand-500"
        >
          <option value="">🌍 Toutes les nations</option>
          {nations.map(n => (
            <option key={n.nation} value={n.nation}>{n.flag_emoji ?? ''} {n.nation}</option>
          ))}
        </select>

        <div className="flex rounded-lg overflow-hidden border border-gray-700">
          {WC_POSITIONS.map(pos => (
            <button
              key={pos || 'all'}
              onClick={() => setPosition(pos)}
              className={clsx(
                'px-3 py-2 text-sm font-medium transition-colors',
                position === pos
                  ? 'bg-brand-600 text-white'
                  : 'bg-gray-800 text-gray-400 hover:bg-gray-700 hover:text-white',
              )}
            >
              {POSITION_LABELS[pos]}
            </button>
          ))}
        </div>

        <div className="relative flex-1 min-w-48">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
          <input
            type="text"
            placeholder="Rechercher un joueur..."
            value={search}
            onChange={e => setSearch(e.target.value)}
            className="w-full pl-9 pr-4 py-2 bg-gray-800 border border-gray-700 rounded-lg text-white placeholder-gray-500 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
          />
        </div>

        <button
          onClick={() => onSwitchToCards(nation || null)}
          className="px-3 py-2 rounded-lg text-sm font-medium border border-gray-700 bg-gray-800 text-gray-400 hover:border-gray-500 hover:text-white transition-colors"
        >
          ⊞ Cartes
        </button>
      </div>

      {error && (
        <div className="mb-4 px-4 py-3 rounded-lg bg-red-900/30 border border-red-700 text-red-300 text-sm">
          {error}
        </div>
      )}

      {/* Table */}
      <div className="bg-gray-800 rounded-xl border border-gray-700 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="border-b border-gray-700">
                <th className="px-4 py-3 text-left text-sm font-medium text-gray-400">Joueur</th>
                <th className="px-3 py-3 text-left text-sm font-medium text-gray-400 hidden sm:table-cell">Nation</th>
                <th className="px-3 py-3 text-left text-sm font-medium text-gray-400 hidden md:table-cell">Club</th>
                <th className="px-3 py-3 text-center text-sm font-medium text-gray-400 hidden md:table-cell">Pos</th>
                <SortTh field="goals" label="Buts" className="text-right hidden sm:table-cell" sortBy={sortBy} sortOrder={sortOrder} onSort={handleSort} />
                <SortTh field="assists" label="PD" className="text-right hidden sm:table-cell" sortBy={sortBy} sortOrder={sortOrder} onSort={handleSort} />
                <SortTh field="xg_per90" label="xG/90" className="text-right hidden sm:table-cell" sortBy={sortBy} sortOrder={sortOrder} onSort={handleSort} />
                <SortTh field="xa_per90" label="xA/90" className="text-right hidden sm:table-cell" sortBy={sortBy} sortOrder={sortOrder} onSort={handleSort} />
                <SortTh field="avg_rating" label="Rating" className="text-right hidden md:table-cell" sortBy={sortBy} sortOrder={sortOrder} onSort={handleSort} />
                <SortTh field="form_xg_5" label="Forme" className="text-right hidden lg:table-cell" sortBy={sortBy} sortOrder={sortOrder} onSort={handleSort} />
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr>
                  <td colSpan={10} className="text-center py-20 text-gray-500 text-sm">Chargement…</td>
                </tr>
              ) : !data || data.players.length === 0 ? (
                <tr>
                  <td colSpan={10} className="text-center py-20 text-gray-500 text-sm">Aucun joueur trouvé</td>
                </tr>
              ) : (
                data.players.map((p: WCPlayer, i: number) => (
                  <tr
                    key={`${p.player_name}-${p.nation ?? ''}-${i}`}
                    className="border-b border-gray-700/50 hover:bg-gray-700/30 transition-colors"
                  >
                    <td className="px-4 py-3">
                      <span className="text-white text-sm font-medium">{p.player_name}</span>
                    </td>
                    <td className="px-3 py-3 hidden sm:table-cell">
                      <span className="text-gray-300 text-sm">{p.nation ?? '—'}</span>
                    </td>
                    <td className="px-3 py-3 hidden md:table-cell">
                      <span className="text-gray-400 text-sm">{p.club ?? '—'}</span>
                    </td>
                    <td className="px-3 py-3 text-center hidden md:table-cell">
                      <span className="text-gray-400 text-xs">{p.position}</span>
                    </td>
                    <td className="px-3 py-3 text-right text-sm text-gray-300 hidden sm:table-cell">
                      {fmtCell(p.goals, 0)}
                    </td>
                    <td className="px-3 py-3 text-right text-sm text-gray-300 hidden sm:table-cell">
                      {fmtCell(p.assists, 0)}
                    </td>
                    <td className="px-3 py-3 text-right text-sm text-gray-300 hidden sm:table-cell">
                      {fmtCell(p.xg_per90, 2)}
                    </td>
                    <td className="px-3 py-3 text-right text-sm text-gray-300 hidden sm:table-cell">
                      {fmtCell(p.xa_per90, 2)}
                    </td>
                    <td className="px-3 py-3 text-right text-sm text-gray-300 hidden md:table-cell">
                      {fmtCell(p.avg_rating)}
                    </td>
                    <td className="px-3 py-3 text-right text-sm text-gray-300 hidden lg:table-cell">
                      {fmtCell(p.form_xg_5)}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        {data && totalPages > 1 && (
          <div className="flex items-center justify-between px-4 py-3 border-t border-gray-700">
            <button
              onClick={() => setPage(p => Math.max(1, p - 1))}
              disabled={page === 1}
              className="px-3 py-1.5 text-sm rounded-lg border border-gray-700 text-gray-400 hover:border-gray-500 hover:text-white disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              ← Précédent
            </button>
            <span className="text-gray-500 text-sm">
              Page {page} / {totalPages} · {data.total} joueurs
            </span>
            <button
              onClick={() => setPage(p => Math.min(totalPages, p + 1))}
              disabled={page === totalPages}
              className="px-3 py-1.5 text-sm rounded-lg border border-gray-700 text-gray-400 hover:border-gray-500 hover:text-white disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              Suivant →
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
