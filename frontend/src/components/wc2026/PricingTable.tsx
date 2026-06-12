'use client'

import { useState } from 'react'
import { clsx } from 'clsx'
import { type WCPlayerPricing } from '@/lib/api'
import { FlagImg } from '@/components/FlagImg'

type Mode = 'goals' | 'assists'
type SortDir = 'asc' | 'desc'

interface PricingTableProps {
  players: WCPlayerPricing[]
  mode: Mode
  nationFlags: Record<string, string | null>
}

// Edge coloré par magnitude
function EdgeBadge({ edge }: { edge: number | null }) {
  if (edge === null) return <span className="text-gray-700">—</span>
  const pct = (edge * 100).toFixed(1)
  const cls =
    edge >= 0.25 ? 'text-emerald-300 font-bold' :
    edge >= 0.12 ? 'text-emerald-400 font-semibold' :
    edge >= 0.05 ? 'text-emerald-500' :
    edge >  0    ? 'text-emerald-700' :
    edge > -0.05 ? 'text-red-700/80' :
    edge > -0.12 ? 'text-red-500' :
    edge > -0.25 ? 'text-red-400' :
                   'text-red-300 font-semibold'
  return (
    <span className={cls}>
      {edge > 0 ? '+' : ''}{pct}%
    </span>
  )
}

// Cote brute neutre (fair prices)
function FairOddsCell({ value }: { value: number | null }) {
  if (!value) return <span className="text-gray-700">—</span>
  return <span className="text-gray-400">{value.toFixed(2)}</span>
}

// Cote bookmaker colorée par rapport à la cote fair
function BkOddsCell({ bk, fair }: { bk: number | null; fair: number | null }) {
  if (!bk) return <span className="text-gray-700">—</span>
  if (!fair) return <span className="text-gray-400">{bk.toFixed(2)}</span>
  const ratio = bk / fair
  const cls =
    ratio >= 1.12 ? 'text-emerald-400 font-semibold' :
    ratio >= 1.05 ? 'text-emerald-600' :
    ratio >= 1.00 ? 'text-gray-300' :
    ratio >= 0.93 ? 'text-red-600/70' :
                    'text-red-500'
  return <span className={clsx('font-mono', cls)}>{bk.toFixed(2)}</span>
}

// Lambda coloré par intensité
function LambdaCell({ value }: { value: number }) {
  const cls =
    value >= 6   ? 'text-white font-bold' :
    value >= 4   ? 'text-orange-200' :
    value >= 2.5 ? 'text-orange-300' :
    value >= 1.2 ? 'text-orange-400' :
    value >= 0.5 ? 'text-orange-500/80' :
                   'text-orange-700/60'
  return <span className={clsx('font-mono', cls)}>{value.toFixed(2)}</span>
}

function SortIcon({ col, sortKey, sortDir }: { col: string; sortKey: string; sortDir: SortDir }) {
  if (col !== sortKey) return <span className="text-gray-700 ml-0.5">↕</span>
  return <span className="text-orange-400 ml-0.5">{sortDir === 'desc' ? '↓' : '↑'}</span>
}

type SortKey = 'player_name' | 'nation' | 'position' | 'lambda' | 'cut1' | 'cut2' | 'cut3' | 'cut4' | 'top' | 'bk' | 'edge'

function sortPlayers(players: WCPlayerPricing[], key: SortKey, dir: SortDir, isGoals: boolean): WCPlayerPricing[] {
  const val = (p: WCPlayerPricing): string | number | null => {
    switch (key) {
      case 'player_name': return p.player_name
      case 'nation':      return p.nation
      case 'position':    return p.position ?? null
      case 'lambda':      return isGoals ? p.lambda_goals : p.lambda_assists
      case 'cut1':        return isGoals ? p.fair_1g : p.fair_1a
      case 'cut2':        return isGoals ? p.fair_2g : p.fair_2a
      case 'cut3':        return isGoals ? p.fair_3g : p.fair_3a
      case 'cut4':        return isGoals ? p.fair_4g : null
      case 'top':         return isGoals ? p.fair_top_scorer : p.fair_top_assister
      case 'bk':          return isGoals ? p.bk_top_scorer : p.bk_top_assister
      case 'edge':        return isGoals ? p.edge_top_scorer : p.edge_top_assister
    }
  }

  return [...players].sort((a, b) => {
    const av = val(a)
    const bv = val(b)
    if (av === null && bv === null) return 0
    if (av === null) return 1
    if (bv === null) return -1
    if (typeof av === 'string' && typeof bv === 'string') {
      return dir === 'asc' ? av.localeCompare(bv) : bv.localeCompare(av)
    }
    return dir === 'asc' ? (av as number) - (bv as number) : (bv as number) - (av as number)
  })
}

export function PricingTable({ players, mode, nationFlags }: PricingTableProps) {
  const isGoals = mode === 'goals'
  const [sortKey, setSortKey] = useState<SortKey>('lambda')
  const [sortDir, setSortDir] = useState<SortDir>('desc')

  function handleSort(col: SortKey) {
    if (col === sortKey) {
      setSortDir(d => d === 'desc' ? 'asc' : 'desc')
    } else {
      setSortKey(col)
      setSortDir('desc')
    }
  }

  const sorted = sortPlayers(players, sortKey, sortDir, isGoals)

  function Th({ col, label, right }: { col: SortKey; label: string; right?: boolean }) {
    return (
      <th
        onClick={() => handleSort(col)}
        className={clsx(
          'py-2 px-2 font-medium cursor-pointer select-none hover:text-white transition-colors',
          right ? 'text-right' : 'text-left',
          sortKey === col ? 'text-orange-400' : 'text-gray-500',
        )}
      >
        {label}<SortIcon col={col} sortKey={sortKey} sortDir={sortDir} />
      </th>
    )
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs text-gray-300">
        <thead>
          <tr className="border-b border-gray-700 uppercase tracking-wider">
            <Th col="player_name" label="Joueur" />
            <Th col="nation"      label="Nat." />
            <Th col="position"    label="Pos" />
            <Th col="lambda"      label="λ"    right />
            <Th col="cut1"        label="≥1"   right />
            <Th col="cut2"        label="≥2"   right />
            <Th col="cut3"        label="≥3"   right />
            {isGoals && <Th col="cut4" label="≥4" right />}
            <Th col="top"  label={isGoals ? 'Top buteur' : 'Top passeur'} right />
            <Th col="bk"   label="BK"   right />
            <Th col="edge" label="Edge" right />
          </tr>
        </thead>
        <tbody>
          {sorted.map((p, i) => {
            const lambda  = isGoals ? p.lambda_goals      : p.lambda_assists
            const cut1    = isGoals ? p.fair_1g           : p.fair_1a
            const cut2    = isGoals ? p.fair_2g           : p.fair_2a
            const cut3    = isGoals ? p.fair_3g           : p.fair_3a
            const cut4    = isGoals ? p.fair_4g           : null
            const fairOut = isGoals ? p.fair_top_scorer   : p.fair_top_assister
            const bkOut   = isGoals ? p.bk_top_scorer     : p.bk_top_assister
            const edgeOut = isGoals ? p.edge_top_scorer   : p.edge_top_assister
            const flag    = nationFlags[p.nation]

            return (
              <tr key={`${p.nation}-${p.player_name}-${i}`} className="border-b border-gray-800/60 hover:bg-gray-800/30">
                <td className="py-1.5 px-2 font-medium text-white text-xs">{p.player_name}</td>
                <td className="py-1.5 px-2">
                  <span className="flex items-center gap-1">
                    <FlagImg emoji={flag} size={14} />
                    <span className="text-gray-500 text-[10px]">{p.nation}</span>
                  </span>
                </td>
                <td className="py-1.5 px-2 text-gray-600 text-xs">{p.position ?? '—'}</td>
                <td className="py-1.5 px-2 text-right"><LambdaCell value={lambda} /></td>
                <td className="py-1.5 px-2 text-right text-xs"><FairOddsCell value={cut1} /></td>
                <td className="py-1.5 px-2 text-right text-xs"><FairOddsCell value={cut2} /></td>
                <td className="py-1.5 px-2 text-right text-xs"><FairOddsCell value={cut3} /></td>
                {isGoals && <td className="py-1.5 px-2 text-right text-xs"><FairOddsCell value={cut4} /></td>}
                <td className="py-1.5 px-2 text-right text-xs"><FairOddsCell value={fairOut} /></td>
                <td className="py-1.5 px-2 text-right text-xs">
                  <BkOddsCell bk={bkOut} fair={fairOut} />
                </td>
                <td className="py-1.5 px-2 text-right text-xs"><EdgeBadge edge={edgeOut} /></td>
              </tr>
            )
          })}
        </tbody>
      </table>
      {players.length === 0 && (
        <p className="text-center text-gray-600 text-sm py-8">Aucune donnée — clique Recalculer</p>
      )}
    </div>
  )
}
