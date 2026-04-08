'use client'

import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  CheckCircle, XCircle, Download,
  ChevronDown, ChevronUp, Loader2, MinusCircle
} from 'lucide-react'
import { clsx } from 'clsx'
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
  stake: number
  edge: number
  status: BetStatus
  pnl: number | null
}

function historyItemToBet(item: HistoryItem): HistoricalBet {
  return {
    id: item.id.toString(),
    date: item.date,
    fixture: item.fixture_name,
    player: item.player_name,
    market: item.market_type as 'goalscorer' | 'assist',
    odds: item.best_odds,
    stake: item.stake ?? 10,
    edge: item.edge,
    status: (item.status || 'running') as BetStatus,
    pnl: item.pnl ?? null,
  }
}

export default function HistoryPage() {
  const queryClient = useQueryClient()
  const [activeTab, setActiveTab] = useState<'approved' | 'autoflat'>('approved')
  const [statusFilter, setStatusFilter] = useState<BetStatus | 'all'>('all')
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

  const allBets = (data?.bets || []).map(historyItemToBet)

  const filteredBets = allBets
    .sort((a, b) => {
      const mult = sortDir === 'asc' ? 1 : -1
      if (sortField === 'date') return mult * (new Date(b.date).getTime() - new Date(a.date).getTime())
      if (sortField === 'pnl') return mult * ((a.pnl ?? 0) - (b.pnl ?? 0))
      return mult * (a.edge - b.edge)
    })

  const stats = {
    total: allBets.length,
    won: allBets.filter(b => b.status === 'won').length,
    lost: allBets.filter(b => b.status === 'lost').length,
    running: allBets.filter(b => b.status === 'running').length,
    totalPnl: allBets.reduce((sum, b) => sum + (b.pnl ?? 0), 0),
  }

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

      {/* Tabs */}
      <div className="flex gap-1 bg-gray-800 rounded-lg p-1 mb-6 w-fit">
        {(['approved', 'autoflat'] as const).map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
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
          label="P&L"
          value={isLoading ? '...' : `${stats.totalPnl >= 0 ? '+' : ''}${stats.totalPnl.toFixed(2)}€`}
          color={stats.totalPnl >= 0 ? 'green' : 'red'}
        />
      </div>

      {/* Filters (Approuvés tab only) */}
      {activeTab === 'approved' && (
        <div className="flex gap-2 mb-4">
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
                <th className="px-4 py-3 text-left text-sm font-medium text-gray-400 hidden md:table-cell">Match</th>
                <th className="px-4 py-3 text-left text-sm font-medium text-gray-400">Joueur</th>
                <th className="px-4 py-3 text-left text-sm font-medium text-gray-400 hidden md:table-cell">Marché</th>
                <th className="px-4 py-3 text-right text-sm font-medium text-gray-400">Cote</th>
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
                  <td colSpan={8} className="px-4 py-8 text-center text-gray-400">
                    Chargement...
                  </td>
                </tr>
              ) : filteredBets.length === 0 ? (
                <tr>
                  <td colSpan={8} className="px-4 py-8 text-center text-gray-400">
                    Aucun pari trouvé
                  </td>
                </tr>
              ) : (
                filteredBets.map((bet) => (
                  <tr key={bet.id} className="border-b border-gray-700/50 hover:bg-ev-surface2">
                    <td className="px-4 py-3 text-sm text-gray-300">
                      {new Date(bet.date).toLocaleDateString('fr-FR')}
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

function ResultActions({
  betId,
  currentStatus,
  onSettle,
  isLoading,
}: {
  betId: string
  currentStatus: BetStatus
  onSettle: (result: 'won' | 'lost' | 'void') => void
  isLoading: boolean
}) {
  if (isLoading) {
    return <Loader2 className="w-4 h-4 animate-spin text-gray-400 mx-auto" />
  }

  return (
    <div className="flex items-center justify-center gap-1">
      <button
        onClick={() => onSettle('won')}
        title="Gagné"
        className={clsx('p-1 transition-colors', currentStatus === 'won' ? 'text-green-400' : 'text-gray-500 hover:text-green-400')}
      >
        <CheckCircle className="w-4 h-4" />
      </button>
      <button
        onClick={() => onSettle('lost')}
        title="Perdu"
        className={clsx('p-1 transition-colors', currentStatus === 'lost' ? 'text-red-400' : 'text-gray-500 hover:text-red-400')}
      >
        <XCircle className="w-4 h-4" />
      </button>
      <button
        onClick={() => onSettle('void')}
        title="Annulé"
        className={clsx('p-1 transition-colors', currentStatus === 'void' ? 'text-gray-300' : 'text-gray-500 hover:text-gray-300')}
      >
        <MinusCircle className="w-4 h-4" />
      </button>
    </div>
  )
}

function StatBox({ label, value, color }: { label: string; value: string | number; color?: string }) {
  const colorClass = color === 'green' ? 'text-green-400'
    : color === 'red' ? 'text-red-400'
    : 'text-white'

  return (
    <div className="bg-gray-800 rounded-lg p-4">
      <p className="text-xs text-gray-500">{label}</p>
      <p className={clsx('text-xl font-bold', colorClass)}>{value}</p>
    </div>
  )
}

function StatusBadge({ status }: { status: BetStatus }) {
  switch (status) {
    case 'won':
      return <CheckCircle className="w-5 h-5 text-green-400 mx-auto" />
    case 'lost':
      return <XCircle className="w-5 h-5 text-red-400 mx-auto" />
    case 'running':
      return <span className="px-2 py-0.5 rounded text-xs bg-gray-600 text-gray-300">Running</span>
    default:
      return <span className="text-gray-500">—</span>
  }
}

function SortButton({
  field, current, dir, onClick, setDir, children
}: {
  field: string
  current: string
  dir: 'asc' | 'desc'
  onClick: (f: any) => void
  setDir: (d: 'asc' | 'desc') => void
  children: React.ReactNode
}) {
  const isActive = field === current

  return (
    <button
      onClick={() => {
        if (isActive) {
          setDir(dir === 'asc' ? 'desc' : 'asc')
        } else {
          onClick(field)
          setDir('desc')
        }
      }}
      className="flex items-center gap-1 hover:text-ev-t1 transition-colors"
    >
      {children}
      {isActive && (dir === 'desc' ? <ChevronDown className="w-3 h-3" /> : <ChevronUp className="w-3 h-3" />)}
    </button>
  )
}
