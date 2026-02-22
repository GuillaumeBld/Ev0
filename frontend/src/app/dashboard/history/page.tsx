'use client'

import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  CheckCircle, XCircle, Clock, Filter, Download,
  ChevronDown, ChevronUp
} from 'lucide-react'
import { clsx } from 'clsx'
import { getHistory } from '@/lib/api'
import type { HistoryItem } from '@/lib/api'

type BetStatus = 'won' | 'lost' | 'pending' | 'void'

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
  pnl: number
}

function historyItemToBet(item: HistoryItem): HistoricalBet {
  return {
    id: item.id.toString(),
    date: item.date,
    fixture: item.fixture_name,
    player: item.player_name,
    market: item.market_type as 'goalscorer' | 'assist',
    odds: item.best_odds,
    stake: 10,
    edge: item.edge,
    status: (item.status || 'pending') as BetStatus,
    pnl: item.pnl ?? 0,
  }
}

export default function HistoryPage() {
  const [statusFilter, setStatusFilter] = useState<BetStatus | 'all'>('all')
  const [sortField, setSortField] = useState<'date' | 'pnl' | 'edge'>('date')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')

  const { data, isLoading } = useQuery({
    queryKey: ['history', statusFilter],
    queryFn: () => getHistory(statusFilter !== 'all' ? { status: statusFilter } : {}),
  })

  const allBets = (data?.bets || []).map(historyItemToBet)

  const filteredBets = allBets
    .sort((a, b) => {
      const mult = sortDir === 'asc' ? 1 : -1
      if (sortField === 'date') return mult * (new Date(b.date).getTime() - new Date(a.date).getTime())
      if (sortField === 'pnl') return mult * (a.pnl - b.pnl)
      return mult * (a.edge - b.edge)
    })

  const stats = {
    total: allBets.length,
    won: allBets.filter(b => b.status === 'won').length,
    lost: allBets.filter(b => b.status === 'lost').length,
    pending: allBets.filter(b => b.status === 'pending').length,
    totalPnl: allBets.reduce((sum, b) => sum + b.pnl, 0),
  }

  return (
    <div className="p-4 md:p-8">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-8">
        <div>
          <h1 className="text-2xl font-bold text-white">Historique</h1>
          <p className="text-gray-400 mt-1">
            {stats.total} paris · {stats.won}W / {stats.lost}L
          </p>
        </div>
        <button className="flex items-center gap-2 px-4 py-2 bg-gray-700 hover:bg-gray-600 text-white rounded-lg transition-colors">
          <Download className="w-4 h-4" />
          Export CSV
        </button>
      </div>

      {/* Stats Summary */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-4 mb-6">
        <StatBox label="Total" value={isLoading ? '...' : stats.total} />
        <StatBox label="Gagnés" value={isLoading ? '...' : stats.won} color="green" />
        <StatBox label="Perdus" value={isLoading ? '...' : stats.lost} color="red" />
        <StatBox label="En cours" value={isLoading ? '...' : stats.pending} color="yellow" />
        <StatBox
          label="P&L"
          value={isLoading ? '...' : `${stats.totalPnl >= 0 ? '+' : ''}${stats.totalPnl.toFixed(2)}€`}
          color={stats.totalPnl >= 0 ? 'green' : 'red'}
        />
      </div>

      {/* Filters */}
      <div className="flex gap-2 mb-4">
        {(['all', 'won', 'lost', 'pending'] as const).map((status) => (
          <button
            key={status}
            onClick={() => setStatusFilter(status)}
            className={clsx(
              'px-3 py-1.5 rounded-lg text-sm transition-colors',
              statusFilter === status
                ? 'bg-brand-600 text-white'
                : 'bg-gray-800 text-gray-400 hover:text-white'
            )}
          >
            {status === 'all' ? 'Tous' : status === 'won' ? 'Gagnés' : status === 'lost' ? 'Perdus' : 'En cours'}
          </button>
        ))}
      </div>

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
              <th className="px-4 py-3 text-right text-sm font-medium text-gray-400 hidden md:table-cell">Mise</th>
              <th className="px-4 py-3 text-center text-sm font-medium text-gray-400">Status</th>
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
                <td colSpan={9} className="px-4 py-8 text-center text-gray-400">
                  Chargement...
                </td>
              </tr>
            ) : filteredBets.length === 0 ? (
              <tr>
                <td colSpan={9} className="px-4 py-8 text-center text-gray-400">
                  Aucun pari trouvé
                </td>
              </tr>
            ) : (
              filteredBets.map((bet) => (
                <tr key={bet.id} className="border-b border-gray-700/50 hover:bg-gray-700/30">
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
                  <td className="px-4 py-3 text-sm text-right text-gray-300 hidden md:table-cell">{bet.stake}€</td>
                  <td className="px-4 py-3 text-center">
                    <StatusBadge status={bet.status} />
                  </td>
                  <td className={clsx(
                    'px-4 py-3 text-sm text-right font-medium',
                    bet.pnl > 0 ? 'text-green-400' : bet.pnl < 0 ? 'text-red-400' : 'text-gray-400'
                  )}>
                    {bet.pnl > 0 ? '+' : ''}{bet.pnl.toFixed(2)}€
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

function StatBox({ label, value, color }: { label: string; value: string | number; color?: string }) {
  const colorClass = color === 'green' ? 'text-green-400'
    : color === 'red' ? 'text-red-400'
    : color === 'yellow' ? 'text-yellow-400'
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
    case 'pending':
      return <Clock className="w-5 h-5 text-yellow-400 mx-auto" />
    default:
      return <span className="text-gray-500">-</span>
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
      className="flex items-center gap-1 hover:text-white transition-colors"
    >
      {children}
      {isActive && (dir === 'desc' ? <ChevronDown className="w-3 h-3" /> : <ChevronUp className="w-3 h-3" />)}
    </button>
  )
}
