'use client'

import { useState } from 'react'
import { clsx } from 'clsx'
import { type WCPlayerPricing } from '@/lib/api'
import { FlagImg } from '@/components/FlagImg'

type Mode = 'goals' | 'assists' | 'decisive'
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

// Cote proposée = fair / marge (équivalent 1/(p×marge))
function OfferedOddsCell({ fair, margin }: { fair: number | null; margin: number }) {
  if (!fair) return <span className="text-gray-700">—</span>
  const offered = Math.max(1.01, fair / margin)
  return <span className="text-amber-300 font-mono tabular-nums">{offered.toFixed(2)}</span>
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

// ── Helpers ──────────────────────────────────────────────────────────────────

const ga = (p: WCPlayerPricing) => (p.wc_goals ?? 0) + (p.wc_assists ?? 0)

// ── Sort ─────────────────────────────────────────────────────────────────────

type SortKey =
  | 'player_name' | 'nation' | 'position'
  | 'ga' | 'lambda'
  | 'top' | 'top3' | 'bk_uni' | 'bk_bet' | 'bk_pmu' | 'bk' | 'edge'

function sortPlayers(
  players: WCPlayerPricing[],
  key: SortKey,
  dir: SortDir,
  mode: Mode,
): WCPlayerPricing[] {
  const val = (p: WCPlayerPricing): string | number | null => {
    switch (key) {
      case 'player_name': return p.player_name
      case 'nation':      return p.nation
      case 'position':    return p.position ?? null
      case 'ga':          return ga(p)
      case 'lambda':
        return mode === 'goals'   ? p.lambda_goals :
               mode === 'assists' ? p.lambda_assists :
                                    p.lambda_goals + p.lambda_assists
      case 'top':
        return mode === 'goals'   ? p.fair_top_scorer :
               mode === 'assists' ? p.fair_top_assister :
                                    p.fair_most_decisive
      case 'top3':
        return mode === 'goals'   ? p.fair_top3_scorer :
               mode === 'assists' ? p.fair_top3_assister :
                                    p.fair_top3_decisive
      case 'bk':          return mode === 'goals' ? p.bk_top_scorer         : p.bk_top_assister
      case 'bk_uni':      return mode === 'goals' ? p.bk_top_scorer_unibet  : p.bk_top_assister_unibet
      case 'bk_bet':      return mode === 'goals' ? p.bk_top_scorer_betclic : p.bk_top_assister_betclic
      case 'bk_pmu':      return mode === 'goals' ? p.bk_top_scorer_pmu     : p.bk_top_assister_pmu
      case 'edge':        return mode === 'goals' ? p.edge_top_scorer       : p.edge_top_assister
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
  const isDecisive = mode === 'decisive'
  const [sortKey, setSortKey] = useState<SortKey>(isDecisive ? 'ga' : 'lambda')
  const [sortDir, setSortDir] = useState<SortDir>('desc')
  const [marginPct, setMarginPct] = useState('110')

  const margin = Math.max(1, (parseFloat(marginPct) || 110) / 100)

  function handleSort(col: SortKey) {
    if (col === sortKey) setSortDir(d => d === 'desc' ? 'asc' : 'desc')
    else { setSortKey(col); setSortDir('desc') }
  }

  const sorted = sortPlayers(players, sortKey, sortDir, mode)

  // Rang au classement décisif (égalités partagées : 1,1,3…)
  const gaValues = players.map(ga)
  const rankOf = (p: WCPlayerPricing) => 1 + gaValues.filter(v => v > ga(p)).length

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

  const topLabel  = isGoals ? 'Top Bt.' : mode === 'assists' ? 'Top Pa.' : 'Top Déc.'
  const topTitle  = isGoals ? 'Cote fair top buteur (dead-heat)'
    : mode === 'assists' ? 'Cote fair top passeur (dead-heat)'
    : 'Cote fair joueur le plus décisif — buts + passes (dead-heat)'
  const top3Title = isGoals ? 'Cote fair top 3 buteurs (dead-heat)'
    : mode === 'assists' ? 'Cote fair top 3 passeurs (dead-heat)'
    : 'Cote fair top 3 du classement décisif (dead-heat)'

  return (
    <div className="overflow-x-auto">
      <div className="flex items-center justify-end gap-2 pb-2 text-[11px] text-gray-500">
        <label htmlFor="wc-margin">Marge book</label>
        <input
          id="wc-margin"
          type="number"
          min={100}
          max={150}
          value={marginPct}
          onChange={e => setMarginPct(e.target.value)}
          className="w-16 bg-gray-800/60 border border-gray-700 rounded px-1.5 py-0.5 text-right text-gray-300 focus:outline-none focus:border-orange-500/60"
        />
        <span>%</span>
        <span className="text-gray-700">·</span>
        <span className="text-amber-300/70">cotes proposées = fair / marge</span>
      </div>
      <table className="w-full text-xs text-gray-300">
        <thead>
          <tr className="border-b border-gray-700 uppercase tracking-wider text-[10px]">
            {isDecisive && (
              <th className="py-2 px-2 text-right text-[10px] font-medium text-gray-600" title="Rang classement décisif (G+A)">#</th>
            )}
            <Th col="player_name" label="Joueur" />
            <Th col="nation"      label="Nat." />
            <Th col="position"    label="Pos" />
            {/* WC actual stats */}
            <th className="py-2 px-2 text-right text-[10px] font-medium text-gray-600 whitespace-nowrap border-l border-gray-800/60"
                title="Buts marqués en tournoi">Buts</th>
            <th className="py-2 px-2 text-right text-[10px] font-medium text-gray-600 whitespace-nowrap"
                title="Passes décisives en tournoi">PD</th>
            {isDecisive ? (
              <Th col="ga" label="G+A" right title="Buts + passes décisives en tournoi" />
            ) : (
              <>
                <th className="py-2 px-2 text-right text-[10px] font-medium text-gray-600 whitespace-nowrap"
                    title="xG/90 min réel en tournoi">WC xG/90</th>
                <th className="py-2 px-2 text-right text-[10px] font-medium text-gray-600 whitespace-nowrap"
                    title="xG/90 blendé (prior scouting + WC réel)">Blend xG/90</th>
              </>
            )}
            {/* Pricing */}
            <Th col="lambda" label="λ tot." right
              title={isDecisive ? 'Lambda G+A total projeté tournoi' : 'Lambda total projeté tournoi'} />
            <Th col="top" label={topLabel} right title={topTitle} />
            <th className="py-2 px-2 text-right text-[10px] font-medium text-amber-400/70 whitespace-nowrap"
                title="Cote proposée (fair / marge)">Prop.</th>
            <Th col="top3" label="Top 3" right title={top3Title} />
            <th className="py-2 px-2 text-right text-[10px] font-medium text-amber-400/70 whitespace-nowrap"
                title="Cote top 3 proposée (fair / marge)">Prop.</th>

            {/* Bookmakers — pas de cotes books pour le marché décisif */}
            {!isDecisive && (
              <>
                <ThSep label="UNI" />
                <ThSep label="BET" />
                <ThSep label="PMU" />
                <Th col="edge" label="Edge" right />
              </>
            )}
          </tr>
        </thead>
        <tbody>
          {sorted.map((p, i) => {
            const lambda  = isGoals ? p.lambda_goals
              : mode === 'assists' ? p.lambda_assists
              : p.lambda_goals + p.lambda_assists
            const fairOut = isGoals ? p.fair_top_scorer
              : mode === 'assists' ? p.fair_top_assister
              : p.fair_most_decisive
            const fairTop3 = isGoals ? p.fair_top3_scorer
              : mode === 'assists' ? p.fair_top3_assister
              : p.fair_top3_decisive
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
                {isDecisive && (
                  <td className="py-1.5 px-2 text-right font-mono text-gray-500">{rankOf(p)}</td>
                )}
                <td className="py-1.5 px-2 font-medium text-white text-xs">{p.player_name}</td>
                <td className="py-1.5 px-2">
                  <span className="flex items-center gap-1">
                    <FlagImg emoji={flag} size={13} />
                    <span className="text-gray-500 text-[10px]">{p.nation}</span>
                  </span>
                </td>
                <td className="py-1.5 px-2 text-gray-600">{p.position ?? '—'}</td>
                {/* WC actual stats */}
                <td className="py-1.5 px-2 text-right font-mono border-l border-gray-800/60">
                  {(p.wc_goals ?? 0) > 0
                    ? <span className="text-emerald-400 font-semibold">{p.wc_goals}</span>
                    : <span className="text-gray-700">0</span>}
                </td>
                <td className="py-1.5 px-2 text-right font-mono">
                  {(p.wc_assists ?? 0) > 0
                    ? <span className="text-blue-400 font-semibold">{p.wc_assists}</span>
                    : <span className="text-gray-700">0</span>}
                </td>
                {isDecisive ? (
                  <td className="py-1.5 px-2 text-right font-mono">
                    {ga(p) > 0
                      ? <span className="text-white font-semibold">{ga(p)}</span>
                      : <span className="text-gray-700">0</span>}
                  </td>
                ) : (
                  <>
                    <td className="py-1.5 px-2 text-right font-mono text-gray-500 text-[11px]">
                      {p.wc_xg_per_90 != null && p.wc_minutes != null && p.wc_minutes >= 45
                        ? p.wc_xg_per_90.toFixed(2)
                        : <span className="text-gray-700">—</span>}
                    </td>
                    <td className="py-1.5 px-2 text-right font-mono text-orange-400/70 text-[11px]">
                      {p.blended_xg_p90 != null ? p.blended_xg_p90.toFixed(2) : <span className="text-gray-700">—</span>}
                    </td>
                  </>
                )}
                <td className="py-1.5 px-2 text-right"><LambdaCell value={lambda} /></td>
                <td className="py-1.5 px-2 text-right"><FairOddsCell value={fairOut} /></td>
                <td className="py-1.5 px-2 text-right"><OfferedOddsCell fair={fairOut} margin={margin} /></td>
                <td className="py-1.5 px-2 text-right"><FairOddsCell value={fairTop3} /></td>
                <td className="py-1.5 px-2 text-right"><OfferedOddsCell fair={fairTop3} margin={margin} /></td>

                {!isDecisive && (
                  <>
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
                  </>
                )}
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
