'use client'

import { useState, useEffect } from 'react'
import { clsx } from 'clsx'
import { RefreshCw } from 'lucide-react'
import {
  type WCMatchListItem,
  type WCMatchDetail,
  getWCMatches,
  getWCMatchDetail,
} from '@/lib/api'
import { MatchDetailPanel } from '@/components/wc2026/MatchDetailPanel'

type StatusFilter = 'all' | 'finished' | 'upcoming'

function statusColor(status: string | null) {
  if (status === 'finished') return 'text-gray-500'
  if (status === 'live' || status === 'inprogress') return 'text-green-400'
  return 'text-gray-600'
}

function formatDate(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString('fr-FR', {
    day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
    timeZone: 'Europe/Paris',
  })
}

function MatchCard({
  match,
  selected,
  loading,
  onClick,
}: {
  match: WCMatchListItem
  selected: boolean
  loading: boolean
  onClick: () => void
}) {
  const finished = match.status === 'finished'
  const hasScore = match.home_score !== null

  return (
    <button
      onClick={onClick}
      className={clsx(
        'w-full text-left px-4 py-3 rounded-lg border transition-colors',
        selected
          ? 'bg-orange-500/10 border-orange-500/40'
          : 'border-gray-700/60 hover:bg-gray-800/50 hover:border-gray-600',
        loading && 'opacity-60',
      )}
    >
      {/* Date */}
      <div className="flex items-center justify-between mb-1.5">
        <span className="text-xs text-gray-500">{formatDate(match.event_date)}</span>
        {match.round_number && (
          <span className="text-xs text-gray-600">J{match.round_number}</span>
        )}
      </div>

      {/* Teams + score */}
      <div className="flex items-center gap-2">
        <div className="flex-1 min-w-0">
          <div className="flex items-center justify-between">
            <span className={clsx('text-sm truncate', hasScore && match.home_score! > match.away_score! ? 'text-white font-semibold' : 'text-gray-300')}>
              {match.home_team}
            </span>
            {hasScore && (
              <span className={clsx('font-mono tabular-nums text-base font-bold ml-2 shrink-0', match.home_score! > match.away_score! ? 'text-white' : 'text-gray-400')}>
                {match.home_score}
              </span>
            )}
          </div>
          <div className="flex items-center justify-between mt-0.5">
            <span className={clsx('text-sm truncate', hasScore && match.away_score! > match.home_score! ? 'text-white font-semibold' : 'text-gray-300')}>
              {match.away_team}
            </span>
            {hasScore && (
              <span className={clsx('font-mono tabular-nums text-base font-bold ml-2 shrink-0', match.away_score! > match.home_score! ? 'text-white' : 'text-gray-400')}>
                {match.away_score}
              </span>
            )}
          </div>
        </div>
      </div>

      {/* xG */}
      {finished && match.home_xg !== null && (
        <div className="flex justify-between mt-1.5 text-xs">
          <span className="text-blue-400/70">xG {match.home_xg.toFixed(2)}</span>
          <span className="text-red-400/70">xG {match.away_xg?.toFixed(2)}</span>
        </div>
      )}

      {/* Status badge */}
      {!finished && (
        <div className={clsx('mt-1.5 text-xs uppercase tracking-wider', statusColor(match.status))}>
          {match.status === 'notstarted' ? formatDate(match.event_date) : match.period ?? match.status}
        </div>
      )}
    </button>
  )
}

export default function WC2026MatchesPage() {
  const [matches, setMatches] = useState<WCMatchListItem[]>([])
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState<number | null>(null)
  const [detail, setDetail] = useState<WCMatchDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all')
  const [roundFilter, setRoundFilter] = useState<number | null>(null)

  async function loadMatches() {
    setLoading(true)
    try {
      const data = await getWCMatches()
      setMatches(data)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { loadMatches() }, [])

  // Auto-refresh: 30s if live match, 2min otherwise
  useEffect(() => {
    const hasLive = matches.some(m => m.status === 'live' || m.status === 'inprogress')
    const interval = setInterval(loadMatches, hasLive ? 30_000 : 120_000)
    return () => clearInterval(interval)
  }, [matches])

  async function selectMatch(id: number) {
    if (selected === id) {
      setSelected(null)
      setDetail(null)
      return
    }
    setSelected(id)
    setDetailLoading(true)
    setDetail(null)
    try {
      const d = await getWCMatchDetail(id)
      setDetail(d)
    } finally {
      setDetailLoading(false)
    }
  }

  const rounds = Array.from(new Set(matches.map(m => m.round_number ?? 0))).sort((a, b) => a - b)

  const filtered = matches.filter(m => {
    if (statusFilter === 'finished' && m.status !== 'finished') return false
    if (statusFilter === 'upcoming' && m.status === 'finished') return false
    if (roundFilter !== null && m.round_number !== roundFilter) return false
    return true
  })

  const byRound: Record<number, WCMatchListItem[]> = {}
  for (const m of filtered) {
    const r = m.round_number ?? 0
    ;(byRound[r] ??= []).push(m)
  }

  const roundLabels: Record<number, string> = {
    1: 'Journée 1',
    2: 'Journée 2',
    3: 'Journée 3',
    4: 'Huitièmes de finale',
    5: 'Quarts de finale',
    6: 'Demi-finales',
    7: 'Finale',
  }

  return (
    <div className="flex h-[calc(100vh-4rem)] overflow-hidden gap-0">
      {/* Left: match list */}
      <div className="w-96 shrink-0 border-r border-gray-700 flex flex-col">
        {/* Header + filters */}
        <div className="p-4 border-b border-gray-700 space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="text-base font-semibold text-white">CDM 2026 — Matchs</h2>
            <button
              onClick={loadMatches}
              disabled={loading}
              className="p-1.5 rounded hover:bg-gray-700 text-gray-500 hover:text-white transition-colors disabled:opacity-40"
            >
              <RefreshCw className={clsx('w-4 h-4', loading && 'animate-spin')} />
            </button>
          </div>

          {/* Status filter */}
          <div className="flex gap-1.5">
            {(['all', 'finished', 'upcoming'] as StatusFilter[]).map(f => (
              <button
                key={f}
                onClick={() => setStatusFilter(f)}
                className={clsx(
                  'flex-1 py-1.5 text-xs rounded border transition-colors',
                  statusFilter === f
                    ? 'border-orange-500/50 bg-orange-500/10 text-orange-300'
                    : 'border-gray-700 text-gray-500 hover:text-gray-300',
                )}
              >
                {f === 'all' ? 'Tous' : f === 'finished' ? 'Terminés' : 'À venir'}
              </button>
            ))}
          </div>

          {/* Round filter */}
          <div className="flex gap-1.5 flex-wrap">
            <button
              onClick={() => setRoundFilter(null)}
              className={clsx(
                'px-2 py-1 text-xs rounded border transition-colors',
                roundFilter === null
                  ? 'border-orange-500/50 bg-orange-500/10 text-orange-300'
                  : 'border-gray-700 text-gray-500 hover:text-gray-300',
              )}
            >
              Toutes
            </button>
            {rounds.map(r => (
              <button
                key={r}
                onClick={() => setRoundFilter(r === roundFilter ? null : r)}
                className={clsx(
                  'px-2 py-1 text-xs rounded border transition-colors',
                  roundFilter === r
                    ? 'border-orange-500/50 bg-orange-500/10 text-orange-300'
                    : 'border-gray-700 text-gray-500 hover:text-gray-300',
                )}
              >
                J{r}
              </button>
            ))}
          </div>
        </div>

        {/* Match list */}
        <div className="flex-1 overflow-y-auto p-3 space-y-4">
          {loading ? (
            <p className="text-gray-600 text-sm text-center py-8">Chargement…</p>
          ) : (
            Object.entries(byRound)
              .sort(([a], [b]) => Number(a) - Number(b))
              .map(([round, roundMatches]) => (
                <div key={round}>
                  <div className="text-xs font-bold text-gray-500 uppercase tracking-wider mb-2 px-1">
                    {roundLabels[Number(round)] ?? `Journée ${round}`}
                  </div>
                  <div className="space-y-1.5">
                    {roundMatches.map(m => (
                      <MatchCard
                        key={m.bzz_id}
                        match={m}
                        selected={selected === m.bzz_id}
                        loading={detailLoading && selected === m.bzz_id}
                        onClick={() => selectMatch(m.bzz_id)}
                      />
                    ))}
                  </div>
                </div>
              ))
          )}
        </div>
      </div>

      {/* Right: detail panel */}
      <div className="flex-1 overflow-y-auto p-6">
        {!selected && !detail && (
          <div className="flex items-center justify-center h-full text-gray-600 text-sm">
            Sélectionne un match pour voir le détail
          </div>
        )}

        {detailLoading && (
          <div className="flex items-center justify-center h-full text-gray-400 text-sm">
            <RefreshCw className="w-4 h-4 animate-spin mr-2" />
            Chargement…
          </div>
        )}

        {detail && !detailLoading && (
          <div className="max-w-3xl mx-auto">
            <MatchDetailPanel
              match={detail}
              onClose={() => { setSelected(null); setDetail(null) }}
            />
          </div>
        )}
      </div>
    </div>
  )
}
