'use client'

import { useState } from 'react'
import { clsx } from 'clsx'
import { type WCNationOdds, type MarketOdds, type BookmakerOddEntry } from '@/lib/api'

type MarketKey = 'winner' | 'top4' | 'top8' | 'group_stage'
type SortField = 'nation' | 'group' | 'unibet' | 'betclic' | 'pmu' | 'best'
type SortDir = 'asc' | 'desc'

const MARKETS: { key: MarketKey; label: string }[] = [
  { key: 'winner',      label: 'Vainqueur'    },
  { key: 'top4',        label: 'Demi-finales' },
  { key: 'top8',        label: 'Quarts'       },
  { key: 'group_stage', label: 'Phase grp'    },
]

const BOOKMAKERS = ['unibet', 'betclic', 'pmu'] as const
type Bookmaker = typeof BOOKMAKERS[number]

const BK_LABEL: Record<Bookmaker, string> = { unibet: 'Unibet', betclic: 'Betclic', pmu: 'PMU' }
const BK_SHORT: Record<Bookmaker, string> = { unibet: 'UNI', betclic: 'BET', pmu: 'PMU' }

// ── Helpers ─────────────────────────────────────────────────────────────────

function bestEntry(market: MarketOdds): { bk: Bookmaker; odds: number } | null {
  let best: { bk: Bookmaker; odds: number } | null = null
  for (const bk of BOOKMAKERS) {
    const e = market[bk]
    if (e.odds !== null && e.is_active && (best === null || e.odds > best.odds)) {
      best = { bk, odds: e.odds }
    }
  }
  return best
}

function bestOddsValue(market: MarketOdds): number | null {
  return bestEntry(market)?.odds ?? null
}

function relTime(iso: string | null): string | null {
  if (!iso) return null
  const h = Math.floor((Date.now() - new Date(iso).getTime()) / 3_600_000)
  if (h < 1) return '<1h'
  if (h < 24) return `${h}h`
  return `${Math.floor(h / 24)}j`
}

// Couleur basée sur la probabilité implicite (1/cote) — indépendante du marché
function oddsTextColor(odds: number): string {
  const p = 1 / odds
  if (p > 0.45) return 'text-amber-300'      // grand favori  odds < 2.2
  if (p > 0.20) return 'text-yellow-200/90'  // favori        odds 2.2–5
  if (p > 0.08) return 'text-gray-100'       // challenger    odds 5–12.5
  if (p > 0.03) return 'text-gray-400'       // outsider      odds 12.5–33
  return 'text-gray-600'                     // longshot      odds > 33
}

function activeCount(nations: WCNationOdds[], key: MarketKey): number {
  return nations.reduce(
    (n, row) => n + BOOKMAKERS.filter(b => row[key][b].odds !== null && row[key][b].is_active).length,
    0,
  )
}

// ── Sub-components ───────────────────────────────────────────────────────────

function OddsChip({ entry, best }: { entry: BookmakerOddEntry; best: number | null }) {
  if (entry.odds === null) {
    return <span className="text-gray-700 text-[12px]">—</span>
  }

  const suspended   = !entry.is_active
  const republished = !!entry.republished_at &&
    Date.now() - new Date(entry.republished_at).getTime() < 48 * 3_600_000
  const isBest = !suspended && entry.odds === best

  const tooltip = suspended
    ? `Suspendu — vu il y a ${relTime(entry.last_seen_at)}`
    : republished
    ? `Republié il y a ${relTime(entry.republished_at)}, cote modifiée il y a ${relTime(entry.odds_changed_at)}`
    : entry.last_seen_at
    ? `Mis à jour il y a ${relTime(entry.last_seen_at)}`
    : undefined

  return (
    <span
      title={tooltip}
      className={clsx(
        'inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded font-mono tabular-nums text-[12px]',
        isBest   && 'bg-green-900/25 ring-1 ring-green-800/40',
        suspended && 'opacity-70',
      )}
    >
      <span className={clsx(
        suspended
          ? 'line-through decoration-amber-500/50 text-amber-500/70'
          : isBest
          ? 'text-green-400 font-semibold'
          : oddsTextColor(entry.odds),
      )}>
        {entry.odds.toFixed(2)}
      </span>
      {suspended  && <span className="text-[9px] text-amber-500/70 leading-none">⚠</span>}
      {republished && !suspended && <span className="text-[9px] text-yellow-400 leading-none">↺</span>}
    </span>
  )
}

function BestCell({ market }: { market: MarketOdds }) {
  const b = bestEntry(market)
  if (!b) return <span className="text-gray-700 text-xs">—</span>
  return (
    <span className="inline-flex items-center gap-1">
      <span className="font-mono font-semibold text-[13px] text-green-400">{b.odds.toFixed(2)}</span>
      <span className="text-[10px] text-green-600/80 bg-green-900/30 px-1 py-px rounded leading-none">
        {BK_SHORT[b.bk]}
      </span>
    </span>
  )
}

// ── Main component ────────────────────────────────────────────────────────────

interface Props {
  nations: WCNationOdds[]
}

const COLS: { f: SortField; label: string; align: 'left' | 'right' }[] = [
  { f: 'nation',  label: 'Nation',   align: 'left'  },
  { f: 'group',   label: 'Grp',      align: 'left'  },
  { f: 'unibet',  label: 'Unibet',   align: 'right' },
  { f: 'betclic', label: 'Betclic',  align: 'right' },
  { f: 'pmu',     label: 'PMU',      align: 'right' },
  { f: 'best',    label: 'Meilleur', align: 'right' },
]

export function NationsOddsTable({ nations }: Props) {
  const [market, setMarket]       = useState<MarketKey>('winner')
  const [sortField, setSortField] = useState<SortField>('best')
  const [sortDir, setSortDir]     = useState<SortDir>('asc')

  function switchMarket(key: MarketKey) {
    setMarket(key)
    setSortField('best')
    setSortDir('asc')
  }

  function setSort(f: SortField, dir: SortDir) {
    setSortField(f)
    setSortDir(dir)
  }

  function handleColClick(f: SortField) {
    if (f === sortField) {
      setSortDir(d => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortField(f)
      // texte : alphabétique asc par défaut ; cotes : croissant asc (favoris d'abord)
      setSortDir('asc')
    }
  }

  const getVal = (row: WCNationOdds): string | number => {
    const m = row[market]
    if (sortField === 'nation') return row.nation
    if (sortField === 'group')  return (row.group_letter ?? 'Z') + row.nation
    if (sortField === 'best')   return bestOddsValue(m) ?? Infinity
    const e = m[sortField as Bookmaker]
    return e.is_active && e.odds !== null ? e.odds : Infinity
  }

  const sorted = [...nations].sort((a, b) => {
    const av = getVal(a), bv = getVal(b)
    const cmp = av < bv ? -1 : av > bv ? 1 : 0
    return sortDir === 'asc' ? cmp : -cmp
  })

  if (nations.length === 0) {
    return (
      <div className="p-8 text-center text-gray-500 text-sm">
        Aucune cote — cliquez sur{' '}
        <span className="text-orange-400 font-medium">Sync</span> pour démarrer.
      </div>
    )
  }

  return (
    <div className="space-y-3">
      {/* Market tabs + sort direction control */}
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div className="flex gap-0.5 border-b border-gray-700/50 pb-0">
          {MARKETS.map(m => {
            const cnt = activeCount(nations, m.key)
            return (
              <button
                key={m.key}
                onClick={() => switchMarket(m.key)}
                className={clsx(
                  'px-3 py-1.5 text-xs font-medium border-b-2 -mb-px transition-colors flex items-center gap-1.5 rounded-t',
                  market === m.key
                    ? 'border-orange-500 text-orange-300'
                    : 'border-transparent text-gray-400 hover:text-gray-200',
                )}
              >
                {m.label}
                <span className={clsx(
                  'text-[10px] px-1.5 py-px rounded-sm font-mono tabular-nums leading-none',
                  market === m.key
                    ? 'bg-orange-500/15 text-orange-400'
                    : 'bg-gray-800 text-gray-600',
                )}>
                  {cnt}
                </span>
              </button>
            )
          })}
        </div>

        {/* Contrôle de tri explicite */}
        <div className="flex items-center gap-1 text-[11px] text-gray-500 shrink-0">
          <span className="mr-1">Cotes&nbsp;:</span>
          <button
            onClick={() => setSort('best', 'asc')}
            className={clsx(
              'flex items-center gap-0.5 px-2 py-1 rounded border transition-colors',
              sortField === 'best' && sortDir === 'asc'
                ? 'border-orange-500/50 text-orange-400 bg-orange-500/10'
                : 'border-gray-700 text-gray-500 hover:text-gray-300 hover:border-gray-600',
            )}
          >
            ▲ Croissant
          </button>
          <button
            onClick={() => setSort('best', 'desc')}
            className={clsx(
              'flex items-center gap-0.5 px-2 py-1 rounded border transition-colors',
              sortField === 'best' && sortDir === 'desc'
                ? 'border-orange-500/50 text-orange-400 bg-orange-500/10'
                : 'border-gray-700 text-gray-500 hover:text-gray-300 hover:border-gray-600',
            )}
          >
            ▼ Décroissant
          </button>
          <button
            onClick={() => setSort('group', 'asc')}
            className={clsx(
              'flex items-center gap-0.5 px-2 py-1 rounded border transition-colors',
              sortField === 'group'
                ? 'border-orange-500/50 text-orange-400 bg-orange-500/10'
                : 'border-gray-700 text-gray-500 hover:text-gray-300 hover:border-gray-600',
            )}
          >
            A→Z Groupe
          </button>
        </div>
      </div>

      {/* Table */}
      <div className="overflow-x-auto">
        <table className="w-full text-sm border-collapse">
          <thead>
            <tr className="border-b border-gray-700/70">
              {COLS.map(({ f, label, align }) => (
                <th
                  key={f}
                  onClick={() => handleColClick(f)}
                  className={clsx(
                    'py-2 px-3 text-[11px] font-medium cursor-pointer select-none transition-colors uppercase tracking-wide group',
                    align === 'right' ? 'text-right' : 'text-left',
                    sortField === f ? 'text-orange-400' : 'text-gray-500 hover:text-gray-300',
                  )}
                >
                  <span className="inline-flex items-center gap-1">
                    {align === 'right' && sortField === f && (
                      <span className="text-orange-500/70 text-[10px]">
                        {sortDir === 'asc' ? '▲' : '▼'}
                      </span>
                    )}
                    {label}
                    {align === 'left' && sortField === f && (
                      <span className="text-orange-500/70 text-[10px]">
                        {sortDir === 'asc' ? '▲' : '▼'}
                      </span>
                    )}
                    {sortField !== f && (
                      <span className="text-gray-700 text-[10px] opacity-0 group-hover:opacity-100 transition-opacity">▲▼</span>
                    )}
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.map(row => {
              const m    = row[market]
              const best = bestOddsValue(m)
              return (
                <tr
                  key={row.nation}
                  className="border-b border-gray-800/30 hover:bg-gray-800/20 transition-colors"
                >
                  <td className="py-2 px-3 whitespace-nowrap">
                    <span className="font-medium text-white text-xs">
                      {row.flag_emoji && <span className="mr-1.5">{row.flag_emoji}</span>}
                      {row.nation}
                    </span>
                  </td>
                  <td className="py-2 px-3 text-[11px] text-gray-500 font-mono">
                    {row.group_letter ?? '—'}
                  </td>
                  {BOOKMAKERS.map(bk => (
                    <td key={bk} className="py-2 px-3 text-right">
                      <OddsChip entry={m[bk]} best={best} />
                    </td>
                  ))}
                  <td className="py-2 px-3 text-right">
                    <BestCell market={m} />
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
