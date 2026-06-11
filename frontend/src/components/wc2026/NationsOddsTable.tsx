'use client'

import { useState } from 'react'
import { clsx } from 'clsx'
import { type WCNationOdds, type MarketOdds, type BookmakerOddEntry } from '@/lib/api'

type SortKey = 'nation' | 'group' | 'winner' | 'top4' | 'top8' | 'group_stage'
type SortDir = 'asc' | 'desc'

const MARKET_LABELS: Record<string, string> = {
  winner:      'Vainqueur',
  top4:        'Top 4',
  top8:        'Top 8',
  group_stage: 'Phase grp',
}

const BK_LABELS: Record<string, string> = {
  unibet:  'UNI',
  pmu:     'PMU',
  betclic: 'BET',
}

const BOOKMAKERS = ['unibet', 'pmu', 'betclic'] as const
type Bookmaker = typeof BOOKMAKERS[number]

function bestActiveOdds(market: MarketOdds): number | null {
  const vals = BOOKMAKERS
    .map((b) => market[b])
    .filter((e): e is BookmakerOddEntry => e.odds !== null && e.is_active)
    .map((e) => e.odds as number)
  return vals.length ? Math.max(...vals) : null
}

function bestOddsForSort(row: WCNationOdds, key: SortKey): number {
  if (key === 'winner')      return bestActiveOdds(row.winner)      ?? Infinity
  if (key === 'top4')        return bestActiveOdds(row.top4)        ?? Infinity
  if (key === 'top8')        return bestActiveOdds(row.top8)        ?? Infinity
  if (key === 'group_stage') return bestActiveOdds(row.group_stage) ?? Infinity
  return 0
}

function relTime(iso: string | null): string | null {
  if (!iso) return null
  const diff = Date.now() - new Date(iso).getTime()
  const h = Math.floor(diff / 3_600_000)
  if (h < 1) return 'il y a <1h'
  if (h < 24) return `il y a ${h}h`
  const d = Math.floor(h / 24)
  return `il y a ${d}j`
}

function isRecentlyRepublished(entry: BookmakerOddEntry): boolean {
  if (!entry.republished_at) return false
  return Date.now() - new Date(entry.republished_at).getTime() < 48 * 3_600_000
}

function OddsEntry({ bk, entry, isBest }: {
  bk: Bookmaker
  entry: BookmakerOddEntry
  isBest: boolean
}) {
  if (entry.odds === null) return null

  const suspended = !entry.is_active
  const republished = isRecentlyRepublished(entry)

  const tooltip = [
    suspended ? `Suspendu — vu en dernier ${relTime(entry.last_seen_at)}` : null,
    republished ? `Republié ${relTime(entry.republished_at)} — cote modifiée ${relTime(entry.odds_changed_at)}` : null,
    !suspended && !republished && entry.last_seen_at ? `Mis à jour ${relTime(entry.last_seen_at)}` : null,
  ].filter(Boolean).join(' | ')

  return (
    <span
      className={clsx('text-xs font-mono inline-flex items-center gap-0.5')}
      title={tooltip || undefined}
    >
      <span className="text-gray-600 text-[10px] mr-0.5">{BK_LABELS[bk]}</span>
      {suspended ? (
        <span className="text-amber-500/80 line-through decoration-amber-500/60">
          {(entry.odds).toFixed(2)}
        </span>
      ) : (
        <span className={isBest ? 'text-green-400 font-semibold' : 'text-gray-400'}>
          {(entry.odds).toFixed(2)}
        </span>
      )}
      {suspended && (
        <span className="text-amber-500/80 text-[9px] ml-0.5">⚠</span>
      )}
      {republished && !suspended && (
        <span className="text-yellow-400 text-[9px] ml-0.5" title={`Republié ${relTime(entry.republished_at)}`}>↺</span>
      )}
    </span>
  )
}

function MarketCell({ market }: { market: MarketOdds }) {
  const best = bestActiveOdds(market)
  const hasAny = BOOKMAKERS.some((b) => market[b].odds !== null)
  if (!hasAny) return <span className="text-gray-700 text-xs">—</span>

  return (
    <div className="flex gap-1.5 items-center flex-wrap">
      {BOOKMAKERS.map((b) => (
        <OddsEntry
          key={b}
          bk={b}
          entry={market[b]}
          isBest={market[b].is_active && market[b].odds !== null && market[b].odds === best}
        />
      ))}
    </div>
  )
}

function Th({
  label, sortKey, current, dir, onClick, className,
}: {
  label: string
  sortKey: SortKey
  current: SortKey
  dir: SortDir
  onClick: (k: SortKey) => void
  className?: string
}) {
  return (
    <th
      onClick={() => onClick(sortKey)}
      className={clsx(
        'px-3 py-2 text-left text-xs font-medium text-gray-400 cursor-pointer select-none whitespace-nowrap hover:text-white transition-colors',
        className,
      )}
    >
      {label}
      <span className={clsx('ml-1', current === sortKey ? 'text-orange-400' : 'text-gray-700')}>
        {current === sortKey ? (dir === 'asc' ? '↑' : '↓') : '↕'}
      </span>
    </th>
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
      av = bestOddsForSort(a, sortKey)
      bv = bestOddsForSort(b, sortKey)
    }
    const cmp = av < bv ? -1 : av > bv ? 1 : 0
    return sortDir === 'asc' ? cmp : -cmp
  })

  if (nations.length === 0) {
    return (
      <div className="p-6 text-center text-gray-500 text-sm">
        Aucune cote disponible — cliquez sur <span className="text-orange-400 font-medium">Sync</span> ou lancez <code>sync_wc_outrights.py</code> en local.
      </div>
    )
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm border-collapse">
        <thead>
          <tr className="border-b border-gray-800">
            <Th label="Nation" sortKey="nation" current={sortKey} dir={sortDir} onClick={toggleSort} className="w-40" />
            <Th label="Grp" sortKey="group" current={sortKey} dir={sortDir} onClick={toggleSort} className="w-12" />
            <Th label={MARKET_LABELS.winner} sortKey="winner" current={sortKey} dir={sortDir} onClick={toggleSort} />
            <Th label={MARKET_LABELS.top4} sortKey="top4" current={sortKey} dir={sortDir} onClick={toggleSort} />
            <Th label={MARKET_LABELS.top8} sortKey="top8" current={sortKey} dir={sortDir} onClick={toggleSort} />
            <Th label={MARKET_LABELS.group_stage} sortKey="group_stage" current={sortKey} dir={sortDir} onClick={toggleSort} />
          </tr>
        </thead>
        <tbody>
          {sorted.map((row) => (
            <tr
              key={row.nation}
              className="border-b border-gray-800/50 hover:bg-gray-800/30 transition-colors"
            >
              <td className="px-3 py-1.5 font-medium text-white whitespace-nowrap">
                {row.flag_emoji && <span className="mr-1.5">{row.flag_emoji}</span>}
                {row.nation}
              </td>
              <td className="px-3 py-1.5 text-xs text-gray-500 font-mono">
                {row.group_letter ?? '—'}
              </td>
              <td className="px-3 py-1.5"><MarketCell market={row.winner} /></td>
              <td className="px-3 py-1.5"><MarketCell market={row.top4} /></td>
              <td className="px-3 py-1.5"><MarketCell market={row.top8} /></td>
              <td className="px-3 py-1.5"><MarketCell market={row.group_stage} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
