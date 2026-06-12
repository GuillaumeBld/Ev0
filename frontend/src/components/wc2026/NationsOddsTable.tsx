'use client'

import { useState } from 'react'
import { clsx } from 'clsx'
import { type WCNationOdds, type MarketOdds, type BookmakerOddEntry } from '@/lib/api'

type MarketKey = 'winner' | 'top4' | 'top8' | 'group_stage'
type SortKey = 'nation' | 'group' | MarketKey
type SortDir = 'asc' | 'desc'

const MARKETS: { key: MarketKey; label: string }[] = [
  { key: 'winner',      label: 'Vainqueur' },
  { key: 'top4',        label: 'Demi-fin'  },
  { key: 'top8',        label: 'Top 8'     },
  { key: 'group_stage', label: 'Phase grp' },
]

const BOOKMAKERS = ['unibet', 'betclic', 'pmu'] as const
type Bookmaker = typeof BOOKMAKERS[number]

const BK_LABELS: Record<Bookmaker, string> = {
  unibet:  'UNI',
  betclic: 'BET',
  pmu:     'PMU',
}

function bestActive(market: MarketOdds): number | null {
  const vals = BOOKMAKERS
    .map((b) => market[b])
    .filter((e): e is BookmakerOddEntry => e.odds !== null && e.is_active)
    .map((e) => e.odds as number)
  return vals.length ? Math.max(...vals) : null
}

function bestForSort(row: WCNationOdds, key: MarketKey): number {
  return bestActive(row[key]) ?? Infinity
}

function relTime(iso: string | null): string | null {
  if (!iso) return null
  const h = Math.floor((Date.now() - new Date(iso).getTime()) / 3_600_000)
  if (h < 1) return '<1h'
  if (h < 24) return `${h}h`
  return `${Math.floor(h / 24)}j`
}

function isRecentlyRepublished(e: BookmakerOddEntry): boolean {
  return !!e.republished_at && Date.now() - new Date(e.republished_at).getTime() < 48 * 3_600_000
}

function OddsCell({ entry, isBest }: { entry: BookmakerOddEntry; isBest: boolean }) {
  if (entry.odds === null) {
    return (
      <td className="px-1.5 py-1 text-center text-[11px] text-gray-700 tabular-nums">—</td>
    )
  }

  const suspended   = !entry.is_active
  const republished = isRecentlyRepublished(entry)

  const tooltip = suspended
    ? `Suspendu — vu en dernier il y a ${relTime(entry.last_seen_at)}`
    : republished
    ? `Republié il y a ${relTime(entry.republished_at)}, cote modifiée il y a ${relTime(entry.odds_changed_at)}`
    : entry.last_seen_at
    ? `Mis à jour il y a ${relTime(entry.last_seen_at)}`
    : undefined

  return (
    <td
      className="px-1.5 py-1 text-center tabular-nums"
      title={tooltip}
    >
      <span className={clsx(
        'text-[11px] font-mono',
        suspended
          ? 'text-amber-500/70 line-through decoration-amber-500/50'
          : isBest
          ? 'text-green-400 font-semibold'
          : 'text-gray-300',
      )}>
        {entry.odds.toFixed(2)}
      </span>
      {suspended && (
        <span className="text-amber-500/70 text-[9px] ml-0.5">⚠</span>
      )}
      {republished && !suspended && (
        <span className="text-yellow-400 text-[9px] ml-0.5">↺</span>
      )}
    </td>
  )
}

interface Props {
  nations: WCNationOdds[]
}

export function NationsOddsTable({ nations }: Props) {
  const [sortKey, setSortKey] = useState<SortKey>('group')
  const [sortDir, setSortDir] = useState<SortDir>('asc')

  function toggleSort(key: SortKey) {
    if (key === sortKey) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortKey(key)
      setSortDir('asc')
    }
  }

  const sorted = [...nations].sort((a, b) => {
    let av: string | number
    let bv: string | number
    if (sortKey === 'nation') {
      av = a.nation; bv = b.nation
    } else if (sortKey === 'group') {
      av = (a.group_letter ?? 'Z') + a.nation
      bv = (b.group_letter ?? 'Z') + b.nation
    } else {
      av = bestForSort(a, sortKey)
      bv = bestForSort(b, sortKey)
    }
    const cmp = av < bv ? -1 : av > bv ? 1 : 0
    return sortDir === 'asc' ? cmp : -cmp
  })

  if (nations.length === 0) {
    return (
      <div className="p-6 text-center text-gray-500 text-sm">
        Aucune cote — cliquez sur <span className="text-orange-400 font-medium">Sync</span> ou lancez <code>sync_wc_outrights.py</code> en local.
      </div>
    )
  }

  const sortIcon = (key: SortKey) =>
    sortKey === key ? (sortDir === 'asc' ? ' ↑' : ' ↓') : ''

  return (
    <div className="overflow-x-auto">
      <table className="text-sm border-collapse w-full">
        <thead>
          {/* Ligne 1 : groupes de marchés */}
          <tr className="border-b border-gray-700">
            <th
              rowSpan={2}
              onClick={() => toggleSort('nation')}
              className="px-3 py-1.5 text-left text-xs font-medium text-gray-400 cursor-pointer hover:text-white whitespace-nowrap select-none"
            >
              Nation{sortIcon('nation')}
            </th>
            <th
              rowSpan={2}
              onClick={() => toggleSort('group')}
              className="px-2 py-1.5 text-left text-xs font-medium text-gray-400 cursor-pointer hover:text-white select-none"
            >
              Grp{sortIcon('group')}
            </th>
            {MARKETS.map((m) => (
              <th
                key={m.key}
                colSpan={3}
                onClick={() => toggleSort(m.key)}
                className={clsx(
                  'px-2 py-1.5 text-center text-xs font-medium cursor-pointer select-none hover:text-white transition-colors border-l border-gray-800',
                  sortKey === m.key ? 'text-orange-400' : 'text-gray-400',
                )}
              >
                {m.label}{sortIcon(m.key)}
              </th>
            ))}
          </tr>
          {/* Ligne 2 : labels bookmakers */}
          <tr className="border-b border-gray-700">
            {MARKETS.map((m) =>
              BOOKMAKERS.map((bk, i) => (
                <th
                  key={`${m.key}-${bk}`}
                  className={clsx(
                    'px-1.5 py-1 text-center text-[10px] font-normal text-gray-500 select-none',
                    i === 0 && 'border-l border-gray-800',
                  )}
                >
                  {BK_LABELS[bk]}
                </th>
              ))
            )}
          </tr>
        </thead>
        <tbody>
          {sorted.map((row) => {
            // Calcule best active odds par marché une seule fois par ligne
            const bests: Record<MarketKey, number | null> = {
              winner:      bestActive(row.winner),
              top4:        bestActive(row.top4),
              top8:        bestActive(row.top8),
              group_stage: bestActive(row.group_stage),
            }

            return (
              <tr
                key={row.nation}
                className="border-b border-gray-800/50 hover:bg-gray-800/30 transition-colors"
              >
                <td className="px-3 py-1 font-medium text-white whitespace-nowrap text-xs">
                  {row.flag_emoji && <span className="mr-1">{row.flag_emoji}</span>}
                  {row.nation}
                </td>
                <td className="px-2 py-1 text-[11px] text-gray-500 font-mono">
                  {row.group_letter ?? '—'}
                </td>
                {MARKETS.map((m) =>
                  BOOKMAKERS.map((bk, i) => (
                    <OddsCell
                      key={`${m.key}-${bk}`}
                      entry={row[m.key][bk]}
                      isBest={
                        row[m.key][bk].is_active &&
                        row[m.key][bk].odds !== null &&
                        row[m.key][bk].odds === bests[m.key]
                      }
                    />
                  ))
                )}
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
