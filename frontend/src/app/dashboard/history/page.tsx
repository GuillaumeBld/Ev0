'use client'

import { useState, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  CheckCircle, XCircle, Download,
  ChevronDown, ChevronUp, Loader2, MinusCircle, Calendar
} from 'lucide-react'
import { clsx } from 'clsx'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Cell, ReferenceLine,
} from 'recharts'
import { getHistory, getAutoflatHistory, patchRecommendation, triggerAutoSettle } from '@/lib/api'
import type { HistoryItem } from '@/lib/api'

type BetStatus = 'won' | 'lost' | 'running' | 'void'

interface HistoricalBet {
  id: string
  date: string
  fixture: string
  player: string
  market: 'goalscorer' | 'assist'
  odds: number
  fairOdds: number | null
  stake: number
  edge: number
  status: BetStatus
  pnl: number | null
  decidedUtc: string | null
}

function historyItemToBet(item: HistoryItem): HistoricalBet {
  return {
    id: item.id.toString(),
    date: item.date,
    fixture: item.fixture_name,
    player: item.player_name,
    market: item.market_type as 'goalscorer' | 'assist',
    odds: item.best_odds,
    fairOdds: item.fair_odds ?? null,
    stake: item.stake ?? 10,
    edge: item.edge,
    status: (item.status || 'running') as BetStatus,
    pnl: item.pnl ?? null,
    decidedUtc: item.decided_utc ?? null,
  }
}

// ── Week helpers ──────────────────────────────────────────────────

function getISOWeekKey(dateStr: string): string {
  const d = new Date(dateStr)
  const tmp = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()))
  const day = tmp.getUTCDay() || 7
  tmp.setUTCDate(tmp.getUTCDate() + 4 - day)
  const yearStart = new Date(Date.UTC(tmp.getUTCFullYear(), 0, 1))
  const week = Math.ceil((((tmp.getTime() - yearStart.getTime()) / 86400000) + 1) / 7)
  return `${tmp.getUTCFullYear()}-W${String(week).padStart(2, '0')}`
}

function getWeekStartDate(weekKey: string): Date {
  const [year, w] = weekKey.split('-W').map(Number)
  const jan4 = new Date(Date.UTC(year, 0, 4))
  const day = jan4.getUTCDay() || 7
  jan4.setUTCDate(jan4.getUTCDate() - (day - 1) + (w - 1) * 7)
  return jan4
}

function formatWeekLabel(weekKey: string): string {
  const d = getWeekStartDate(weekKey)
  const day = d.getUTCDate()
  const month = d.toLocaleString('fr-FR', { month: 'short', timeZone: 'UTC' })
  const week = weekKey.split('-W')[1]
  return `S${week} · ${day} ${month}`
}

function formatTime(utc: string | null): string {
  if (!utc) return '—'
  return new Date(utc).toLocaleTimeString('fr-FR', {
    timeZone: 'Europe/Paris',
    hour: '2-digit',
    minute: '2-digit',
  })
}

interface WeekData {
  weekKey: string
  label: string
  bets: number
  wins: number
  losses: number
  pnl: number
  staked: number
  roi: number
}

function buildWeeklyData(bets: HistoricalBet[]): WeekData[] {
  const map = new Map<string, Omit<WeekData, 'roi'>>()
  for (const bet of bets) {
    if (bet.status === 'running') continue
    const key = getISOWeekKey(bet.date)
    if (!map.has(key)) {
      map.set(key, { weekKey: key, label: formatWeekLabel(key), bets: 0, wins: 0, losses: 0, pnl: 0, staked: 0 })
    }
    const w = map.get(key)!
    w.bets++
    if (bet.status === 'won') w.wins++
    if (bet.status === 'lost') w.losses++
    w.pnl += bet.pnl ?? 0
    w.staked += bet.stake
  }
  return Array.from(map.values())
    .sort((a, b) => a.weekKey.localeCompare(b.weekKey))
    .map(w => ({ ...w, roi: w.staked > 0 ? w.pnl / w.staked : 0 }))
}

// ── Custom tooltip for weekly chart ──────────────────────────────

function WeekTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null
  const d = payload[0].payload as WeekData
  return (
    <div className="bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-xs">
      <p className="text-gray-300 font-medium mb-1">{d.label}</p>
      <p className={clsx('font-mono font-semibold', d.pnl >= 0 ? 'text-emerald-400' : 'text-rose-400')}>
        P&L {d.pnl >= 0 ? '+' : ''}{d.pnl.toFixed(2)}€
      </p>
      <p className={clsx('font-mono', d.roi >= 0 ? 'text-emerald-400/70' : 'text-rose-400/70')}>
        ROI {(d.roi * 100).toFixed(1)}%
      </p>
      <p className="text-gray-500 mt-1">{d.wins}W / {d.losses}L ({d.bets} paris)</p>
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────

export default function HistoryPage() {
  const queryClient = useQueryClient()
  const [activeTab, setActiveTab] = useState<'approved' | 'autoflat'>('approved')
  const [statusFilter, setStatusFilter] = useState<BetStatus | 'all'>('all')
  const [weekFilter, setWeekFilter] = useState<string | null>(null)
  const [sortField, setSortField] = useState<'date' | 'pnl' | 'edge'>('date')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')
  const [settling, setSettling] = useState(false)

  const { data: approvedData, isLoading: loadingApproved } = useQuery({
    queryKey: ['history-approved', statusFilter],
    queryFn: () => getHistory(statusFilter !== 'all' ? { status: statusFilter } : {}),
    enabled: activeTab === 'approved',
  })

  const { data: autoflatData, isLoading: loadingAutoflat } = useQuery({
    queryKey: ['history-autoflat'],
    queryFn: () => getAutoflatHistory(),
    enabled: activeTab === 'autoflat',
  })

  const data = activeTab === 'approved' ? approvedData : autoflatData
  const isLoading = activeTab === 'approved' ? loadingApproved : loadingAutoflat

  const settleMutation = useMutation({
    mutationFn: ({ id, result }: { id: string; result: 'won' | 'lost' | 'void' }) =>
      patchRecommendation(id, { result }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['history-approved'] })
      queryClient.invalidateQueries({ queryKey: ['dashboard-stats'] })
    },
  })

  const handleAutoSettle = async () => {
    setSettling(true)
    try {
      const res = await triggerAutoSettle()
      queryClient.invalidateQueries({ queryKey: ['history-approved'] })
      queryClient.invalidateQueries({ queryKey: ['dashboard-stats'] })
      alert(`${res.settled} paris settlés automatiquement`)
    } finally {
      setSettling(false)
    }
  }

  const allBets = useMemo(() => (data?.bets || []).map(historyItemToBet), [data])
  const weeklyData = useMemo(() => buildWeeklyData(allBets), [allBets])

  const filteredBets = useMemo(() => {
    return allBets
      .filter(b => !weekFilter || getISOWeekKey(b.date) === weekFilter)
      .sort((a, b) => {
        const mult = sortDir === 'asc' ? 1 : -1
        if (sortField === 'date') return mult * (new Date(b.date).getTime() - new Date(a.date).getTime())
        if (sortField === 'pnl') return mult * ((a.pnl ?? 0) - (b.pnl ?? 0))
        return mult * (a.edge - b.edge)
      })
  }, [allBets, weekFilter, sortField, sortDir])

  const stats = useMemo(() => ({
    total: allBets.length,
    won: allBets.filter(b => b.status === 'won').length,
    lost: allBets.filter(b => b.status === 'lost').length,
    running: allBets.filter(b => b.status === 'running').length,
    totalPnl: allBets.reduce((sum, b) => sum + (b.pnl ?? 0), 0),
  }), [allBets])

  const weekStats = useMemo(() => {
    if (!weekFilter) return null
    const w = weeklyData.find(w => w.weekKey === weekFilter)
    return w ?? null
  }, [weekFilter, weeklyData])

  return (
    <div className="p-4 md:p-8">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-6">
        <div>
          <h1 className="text-2xl font-bold text-white">Historique</h1>
          <p className="text-gray-400 mt-1">
            {stats.total} paris · {stats.won}W / {stats.lost}L
          </p>
        </div>
        <div className="flex gap-2">
          <button className="flex items-center gap-2 px-4 py-2 bg-gray-700 hover:bg-ev-surface2 text-white rounded-lg transition-colors">
            <Download className="w-4 h-4" />
            Export CSV
          </button>
          <button
            onClick={handleAutoSettle}
            disabled={settling}
            className="flex items-center gap-2 px-4 py-2 bg-brand-600 hover:bg-brand-500 disabled:opacity-50 text-white rounded-lg transition-colors"
          >
            {settling ? <Loader2 className="w-4 h-4 animate-spin" /> : <CheckCircle className="w-4 h-4" />}
            Auto-settle
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 bg-gray-800 rounded-lg p-1 mb-6 w-fit">
        {(['approved', 'autoflat'] as const).map((tab) => (
          <button
            key={tab}
            onClick={() => { setActiveTab(tab); setWeekFilter(null) }}
            className={clsx(
              'px-4 py-2 rounded-md text-sm font-medium transition-colors',
              activeTab === tab ? 'bg-brand-600 text-white' : 'text-gray-400 hover:text-ev-t1'
            )}
          >
            {tab === 'approved' ? 'Approuvés' : 'AutoFlat'}
          </button>
        ))}
      </div>

      {/* Stats Summary */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-4 mb-6">
        <StatBox label="Total" value={isLoading ? '...' : stats.total} />
        <StatBox label="Gagnés" value={isLoading ? '...' : stats.won} color="green" />
        <StatBox label="Perdus" value={isLoading ? '...' : stats.lost} color="red" />
        <StatBox label="Running" value={isLoading ? '...' : stats.running} />
        <StatBox
          label="P&L total"
          value={isLoading ? '...' : `${stats.totalPnl >= 0 ? '+' : ''}${stats.totalPnl.toFixed(2)}€`}
          color={stats.totalPnl >= 0 ? 'green' : 'red'}
        />
      </div>

      {/* Weekly chart */}
      {!isLoading && weeklyData.length > 0 && (
        <div className="bg-gray-800 rounded-xl p-5 mb-6">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-sm font-semibold text-gray-300">P&L par semaine</h2>
            {weekFilter && (
              <button
                onClick={() => setWeekFilter(null)}
                className="text-xs text-brand-400 hover:text-brand-300 transition-colors"
              >
                Voir tout
              </button>
            )}
          </div>
          <ResponsiveContainer width="100%" height={160}>
            <BarChart data={weeklyData} barSize={24} onClick={(e) => {
              if (e?.activePayload?.[0]) {
                const w = e.activePayload[0].payload as WeekData
                setWeekFilter(prev => prev === w.weekKey ? null : w.weekKey)
              }
            }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#374151" vertical={false} />
              <XAxis
                dataKey="label"
                tick={{ fill: '#9ca3af', fontSize: 10 }}
                axisLine={false}
                tickLine={false}
              />
              <YAxis
                tick={{ fill: '#9ca3af', fontSize: 10 }}
                axisLine={false}
                tickLine={false}
                tickFormatter={(v) => `${v >= 0 ? '+' : ''}${v.toFixed(0)}€`}
              />
              <Tooltip content={<WeekTooltip />} cursor={{ fill: 'rgba(255,255,255,0.04)' }} />
              <ReferenceLine y={0} stroke="#4b5563" strokeWidth={1} />
              <Bar dataKey="pnl" radius={[3, 3, 0, 0]}>
                {weeklyData.map((entry) => (
                  <Cell
                    key={entry.weekKey}
                    fill={entry.weekKey === weekFilter
                      ? (entry.pnl >= 0 ? '#34d399' : '#f87171')
                      : (entry.pnl >= 0 ? '#10b981' : '#ef4444')
                    }
                    opacity={weekFilter && entry.weekKey !== weekFilter ? 0.4 : 1}
                  />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
          <p className="text-[10px] text-gray-600 mt-2 text-center">
            Cliquer sur une barre pour filtrer · {weeklyData.length} semaine{weeklyData.length > 1 ? 's' : ''}
          </p>
        </div>
      )}

      {/* Week summary banner when filtered */}
      {weekFilter && weekStats && (
        <div className="flex items-center gap-6 bg-gray-800/60 border border-gray-700 rounded-xl px-5 py-3 mb-4">
          <div className="flex items-center gap-2 text-sm text-gray-300">
            <Calendar className="w-4 h-4 text-brand-400" />
            <span className="font-medium">{weekStats.label}</span>
          </div>
          <span className="text-gray-600">|</span>
          <span className="text-sm text-gray-400">{weekStats.wins}W / {weekStats.losses}L</span>
          <span className={clsx('text-sm font-semibold font-mono', weekStats.pnl >= 0 ? 'text-emerald-400' : 'text-rose-400')}>
            {weekStats.pnl >= 0 ? '+' : ''}{weekStats.pnl.toFixed(2)}€
          </span>
          <span className={clsx('text-xs font-mono', weekStats.roi >= 0 ? 'text-emerald-400/70' : 'text-rose-400/70')}>
            ROI {(weekStats.roi * 100).toFixed(1)}%
          </span>
        </div>
      )}

      {/* Filters */}
      {activeTab === 'approved' && (
        <div className="flex flex-wrap gap-2 mb-4">
          {(['all', 'running', 'won', 'lost'] as const).map((status) => (
            <button
              key={status}
              onClick={() => setStatusFilter(status)}
              className={clsx(
                'px-3 py-1.5 rounded-lg text-sm transition-colors',
                statusFilter === status
                  ? 'bg-brand-600 text-white'
                  : 'bg-gray-800 text-gray-400 hover:text-ev-t1'
              )}
            >
              {status === 'all' ? 'Tous' : status === 'running' ? 'Running' : status === 'won' ? 'Gagnés' : 'Perdus'}
            </button>
          ))}
        </div>
      )}

      {/* Table */}
      <div className="bg-gray-800 rounded-xl overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="border-b border-gray-700">
                <th className="px-4 py-3 text-left text-sm font-medium text-gray-400">
                  <SortButton field="date" current={sortField} dir={sortDir} onClick={setSortField} setDir={setSortDir}>
                    Date
                  </SortButton>
                </th>
                <th className="px-4 py-3 text-left text-sm font-medium text-gray-400 hidden lg:table-cell">Heure</th>
                <th className="px-4 py-3 text-left text-sm font-medium text-gray-400 hidden md:table-cell">Match</th>
                <th className="px-4 py-3 text-left text-sm font-medium text-gray-400">Joueur</th>
                <th className="px-4 py-3 text-left text-sm font-medium text-gray-400 hidden md:table-cell">Marché</th>
                <th className="px-4 py-3 text-right text-sm font-medium text-gray-400">Cote</th>
                <th className="px-4 py-3 text-right text-sm font-medium text-gray-400 hidden lg:table-cell">
                  <span className="text-brand-400">Cote ev0</span>
                </th>
                <th className="px-4 py-3 text-right text-sm font-medium text-gray-400">
                  <SortButton field="edge" current={sortField} dir={sortDir} onClick={setSortField} setDir={setSortDir}>
                    Edge
                  </SortButton>
                </th>
                <th className="px-4 py-3 text-center text-sm font-medium text-gray-400">Résultat</th>
                <th className="px-4 py-3 text-right text-sm font-medium text-gray-400">
                  <SortButton field="pnl" current={sortField} dir={sortDir} onClick={setSortField} setDir={setSortDir}>
                    P&L
                  </SortButton>
                </th>
              </tr>
            </thead>
            <tbody>
              {isLoading ? (
                <tr>
                  <td colSpan={10} className="px-4 py-8 text-center text-gray-400">
                    Chargement...
                  </td>
                </tr>
              ) : filteredBets.length === 0 ? (
                <tr>
                  <td colSpan={10} className="px-4 py-8 text-center text-gray-400">
                    Aucun pari trouvé
                  </td>
                </tr>
              ) : (
                filteredBets.map((bet) => (
                  <tr key={bet.id} className="border-b border-gray-700/50 hover:bg-ev-surface2">
                    <td className="px-4 py-3 text-sm text-gray-300">
                      {new Date(bet.date).toLocaleDateString('fr-FR')}
                    </td>
                    <td className="px-4 py-3 text-sm text-gray-400 font-mono hidden lg:table-cell">
                      {formatTime(bet.decidedUtc)}
                    </td>
                    <td className="px-4 py-3 text-sm text-white hidden md:table-cell">{bet.fixture}</td>
                    <td className="px-4 py-3 text-sm text-white font-medium">{bet.player}</td>
                    <td className="px-4 py-3 hidden md:table-cell">
                      <span className={clsx(
                        'px-2 py-0.5 rounded text-xs',
                        bet.market === 'goalscorer' ? 'bg-orange-500/20 text-orange-400' : 'bg-blue-500/20 text-blue-400'
                      )}>
                        {bet.market === 'goalscorer' ? 'Buteur' : 'Passeur'}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-sm text-right text-gray-300">{bet.odds.toFixed(2)}</td>
                    <td className="px-4 py-3 text-sm text-right hidden lg:table-cell">
                      {bet.fairOdds != null
                        ? <span className="text-brand-400 font-mono">{bet.fairOdds.toFixed(2)}</span>
                        : <span className="text-gray-600">—</span>
                      }
                    </td>
                    <td className="px-4 py-3 text-sm text-right text-green-400">+{(bet.edge * 100).toFixed(1)}%</td>
                    <td className="px-4 py-3 text-center">
                      {activeTab === 'approved' ? (
                        <ResultActions
                          betId={bet.id}
                          currentStatus={bet.status}
                          onSettle={(result) => settleMutation.mutate({ id: bet.id, result })}
                          isLoading={settleMutation.isPending && settleMutation.variables?.id === bet.id}
                        />
                      ) : (
                        <StatusBadge status={bet.status} />
                      )}
                    </td>
                    <td className={clsx(
                      'px-4 py-3 text-sm text-right font-medium',
                      bet.pnl != null && bet.pnl > 0 ? 'text-green-400' :
                      bet.pnl != null && bet.pnl < 0 ? 'text-red-400' : 'text-gray-500'
                    )}>
                      {bet.status === 'running'
                        ? '—'
                        : `${(bet.pnl ?? 0) >= 0 ? '+' : ''}${(bet.pnl ?? 0).toFixed(2)}€`
                      }
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

// ── Sub-components ────────────────────────────────────────────────

function ResultActions({
  betId, currentStatus, onSettle, isLoading,
}: {
  betId: string
  currentStatus: BetStatus
  onSettle: (result: 'won' | 'lost' | 'void') => void
  isLoading: boolean
}) {
  if (isLoading) return <Loader2 className="w-4 h-4 animate-spin text-gray-400 mx-auto" />
  return (
    <div className="flex items-center justify-center gap-1">
      <button onClick={() => onSettle('won')} title="Gagné"
        className={clsx('p-1 transition-colors', currentStatus === 'won' ? 'text-green-400' : 'text-gray-500 hover:text-green-400')}>
        <CheckCircle className="w-4 h-4" />
      </button>
      <button onClick={() => onSettle('lost')} title="Perdu"
        className={clsx('p-1 transition-colors', currentStatus === 'lost' ? 'text-red-400' : 'text-gray-500 hover:text-red-400')}>
        <XCircle className="w-4 h-4" />
      </button>
      <button onClick={() => onSettle('void')} title="Annulé"
        className={clsx('p-1 transition-colors', currentStatus === 'void' ? 'text-gray-300' : 'text-gray-500 hover:text-gray-300')}>
        <MinusCircle className="w-4 h-4" />
      </button>
    </div>
  )
}

function StatBox({ label, value, color }: { label: string; value: string | number; color?: string }) {
  const colorClass = color === 'green' ? 'text-green-400' : color === 'red' ? 'text-red-400' : 'text-white'
  return (
    <div className="bg-gray-800 rounded-lg p-4">
      <p className="text-xs text-gray-500">{label}</p>
      <p className={clsx('text-xl font-bold', colorClass)}>{value}</p>
    </div>
  )
}

function StatusBadge({ status }: { status: BetStatus }) {
  switch (status) {
    case 'won': return <CheckCircle className="w-5 h-5 text-green-400 mx-auto" />
    case 'lost': return <XCircle className="w-5 h-5 text-red-400 mx-auto" />
    case 'running': return <span className="px-2 py-0.5 rounded text-xs bg-gray-600 text-gray-300">Running</span>
    default: return <span className="text-gray-500">—</span>
  }
}

function SortButton({
  field, current, dir, onClick, setDir, children
}: {
  field: string; current: string; dir: 'asc' | 'desc'
  onClick: (f: any) => void; setDir: (d: 'asc' | 'desc') => void
  children: React.ReactNode
}) {
  const isActive = field === current
  return (
    <button
      onClick={() => {
        if (isActive) { setDir(dir === 'asc' ? 'desc' : 'asc') }
        else { onClick(field); setDir('desc') }
      }}
      className="flex items-center gap-1 hover:text-ev-t1 transition-colors"
    >
      {children}
      {isActive && (dir === 'desc' ? <ChevronDown className="w-3 h-3" /> : <ChevronUp className="w-3 h-3" />)}
    </button>
  )
}
