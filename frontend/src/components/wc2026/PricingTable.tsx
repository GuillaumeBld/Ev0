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

// ── Cells ────────────────────────────────────────────────────────────────────

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
  return <span className={cls}>{edge > 0 ? '+' : ''}{pct}%</span>
}

function FairOddsCell({ value }: { value: number | null }) {
  if (!value) return <span className="text-gray-700">—</span>
  return <span className="text-gray-400">{value.toFixed(2)}</span>
}

// Cote BK avec couleur vs fair + highlight si c'est la meilleure parmi les 3 books
function BkOddsCell({
  bk,
  fair,
  isBest,
}: {
  bk: number | null
  fair: number | null
  isBest: boolean
}) {
  if (!bk) return <span className="text-gray-700">—</span>
  const ratio = fair ? bk / fair : null
  const cls =
    isBest              ? 'text-emerald-400 font-semibold' :
    ratio === null       ? 'text-gray-400' :
    ratio >= 1.12        ? 'text-emerald-500' :
    ratio >= 1.05        ? 'text-emerald-600' :
    ratio >= 1.00        ? 'text-gray-300' :
    ratio >= 0.93        ? 'text-red-600/70' :
                           'text-red-500'
  return (
    <span className={clsx(
      'font-mono tabular-nums',
      cls,
      isBest && 'underline decoration-emerald-700/50 decoration-dotted underline-offset-2',
    )}>
      {bk.toFixed(2)}
    </span>
  )
}

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

// ── Sort ─────────────────────────────────────────────────────────────────────

type SortKey =
  | 'player_name' | 'nation' | 'position'
  | 'lambda' | 'cut1' | 'cut2' | 'cut3' | 'cut4'
  | 'top' | 'bk_uni' | 'bk_bet' | 'bk_pmu' | 'bk' | 'edge'

function sortPlayers(
  players: WCPlayerPricing[],
  key: SortKey,
  dir: SortDir,
  isGoals: boolean,
): WCPlayerPricing[] {
  const val = (p: WCPlayerPricing): string | number | null => {
    switch (key) {
      case 'player_name': return p.player_name
      case 'nation':      return p.nation
      case 'position':    return p.position ?? null
      case 'lambda':      return isGoals ? p.lambda_goals        : p.lambda_assists
      case 'cut1':        return isGoals ? p.fair_1g             : p.fair_1a
      case 'cut2':        return isGoals ? p.fair_2g             : p.fair_2a
      case 'cut3':        return isGoals ? p.fair_3g             : p.fair_3a
      case 'cut4':        return isGoals ? p.fair_4g             : null
      case 'top':         return isGoals ? p.fair_top_scorer     : p.fair_top_assister
      case 'bk':          return isGoals ? p.bk_top_scorer       : p.bk_top_assister
      case 'bk_uni':      return isGoals ? p.bk_top_scorer_unibet  : p.bk_top_assister_unibet
      case 'bk_bet':      return isGoals ? p.bk_top_scorer_betclic : p.bk_top_assister_betclic
      case 'bk_pmu':      return isGoals ? p.bk_top_scorer_pmu    : p.bk_top_assister_pmu
      case 'edge':        return isGoals ? p.edge_top_scorer      : p.edge_top_assister
    }
  }

  return [...players].sort((a, b) => {
    const av = val(a), bv = val(b)
    if (av === null && bv === null) return 0
    if (av === null) return 1
    if (bv === null) return -1
    if (typeof av === 'string' && typeof bv === 'string')
      return dir === 'asc' ? av.localeCompare(bv) : bv.localeCompare(av)
    return dir === 'asc' ? (av as number) - (bv as number) : (bv as number) - (av as number)
  })
}

// ── Main ─────────────────────────────────────────────────────────────────────

export function PricingTable({ players, mode, nationFlags }: PricingTableProps) {
  const isGoals = mode === 'goals'
  const [sortKey, setSortKey] = useState<SortKey>('lambda')
  const [sortDir, setSortDir] = useState<SortDir>('desc')

  function handleSort(col: SortKey) {
    if (col === sortKey) setSortDir(d => d === 'desc' ? 'asc' : 'desc')
    else { setSortKey(col); setSortDir('desc') }
  }

  const sorted = sortPlayers(players, sortKey, sortDir, isGoals)

  function Th({ col, label, right, title }: { col: SortKey; label: string; right?: boolean; title?: string }) {
    const active = sortKey === col
    return (
      <th
        onClick={() => handleSort(col)}
        title={title}
        className={clsx(
          'py-2 px-2 font-medium cursor-pointer select-none transition-colors whitespace-nowrap',
          right ? 'text-right' : 'text-left',
          active ? 'text-orange-400' : 'text-gray-500 hover:text-gray-300',
        )}
      >
        {label}
        <span className="ml-0.5 text-[10px]">
          {active ? (sortDir === 'desc' ? '▼' : '▲') : <span className="text-gray-700">▲▼</span>}
        </span>
      </th>
    )
  }

  // Séparateur visuel entre sections
  function ThSep({ label }: { label: string }) {
    return (
      <th className="py-2 px-2 text-center text-[10px] font-normal text-gray-600 uppercase tracking-wider border-l border-gray-800/60 whitespace-nowrap">
        {label}
      </th>
    )
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs text-gray-300">
        <thead>
          <tr className="border-b border-gray-700 uppercase tracking-wider text-[10px]">
            <Th col="player_name" label="Joueur" />
            <Th col="nation"      label="Nat." />
            <Th col="position"    label="Pos" />
            <Th col="lambda"      label="λ" right title="Lambda attendu sur le tournoi" />
            <Th col="cut1"        label="≥1" right />
            <Th col="cut2"        label="≥2" right />
            <Th col="cut3"        label="≥3" right />
            {isGoals && <Th col="cut4" label="≥4" right />}
            <Th col="top" label={isGoals ? 'Top Bt.' : 'Top Pa.'} right
              title={isGoals ? 'Cote fair top buteur' : 'Cote fair top passeur'} />

            {/* Bookmakers — 3 colonnes */}
            <ThSep label="UNI" />
            <ThSep label="BET" />
            <ThSep label="PMU" />

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
            const bkUni   = isGoals ? p.bk_top_scorer_unibet  : p.bk_top_assister_unibet
            const bkBet   = isGoals ? p.bk_top_scorer_betclic : p.bk_top_assister_betclic
            const bkPmu   = isGoals ? p.bk_top_scorer_pmu     : p.bk_top_assister_pmu
            const edgeOut = isGoals ? p.edge_top_scorer   : p.edge_top_assister
            const flag    = nationFlags[p.nation]

            // Meilleure cote parmi les 3 books pour highlight
            const bkValues = [bkUni, bkBet, bkPmu].filter((v): v is number => v !== null)
            const bkBest   = bkValues.length ? Math.max(...bkValues) : null

            return (
              <tr
                key={`${p.nation}-${p.player_name}-${i}`}
                className="border-b border-gray-800/50 hover:bg-gray-800/25 transition-colors"
              >
                <td className="py-1.5 px-2 font-medium text-white text-xs">{p.player_name}</td>
                <td className="py-1.5 px-2">
                  <span className="flex items-center gap-1">
                    <FlagImg emoji={flag} size={13} />
                    <span className="text-gray-500 text-[10px]">{p.nation}</span>
                  </span>
                </td>
                <td className="py-1.5 px-2 text-gray-600">{p.position ?? '—'}</td>
                <td className="py-1.5 px-2 text-right"><LambdaCell value={lambda} /></td>
                <td className="py-1.5 px-2 text-right"><FairOddsCell value={cut1} /></td>
                <td className="py-1.5 px-2 text-right"><FairOddsCell value={cut2} /></td>
                <td className="py-1.5 px-2 text-right"><FairOddsCell value={cut3} /></td>
                {isGoals && <td className="py-1.5 px-2 text-right"><FairOddsCell value={cut4} /></td>}
                <td className="py-1.5 px-2 text-right"><FairOddsCell value={fairOut} /></td>

                {/* Unibet */}
                <td className="py-1.5 px-2 text-right border-l border-gray-800/60">
                  <BkOddsCell bk={bkUni} fair={fairOut} isBest={bkUni !== null && bkUni === bkBest} />
                </td>
                {/* Betclic */}
                <td className="py-1.5 px-2 text-right">
                  <BkOddsCell bk={bkBet} fair={fairOut} isBest={bkBet !== null && bkBet === bkBest} />
                </td>
                {/* PMU */}
                <td className="py-1.5 px-2 text-right">
                  <BkOddsCell bk={bkPmu} fair={fairOut} isBest={bkPmu !== null && bkPmu === bkBest} />
                </td>

                <td className="py-1.5 px-2 text-right"><EdgeBadge edge={edgeOut} /></td>
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
