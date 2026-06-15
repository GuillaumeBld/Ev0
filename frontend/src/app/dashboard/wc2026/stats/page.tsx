'use client'

import { useState, useEffect, useMemo } from 'react'
import { RefreshCw, ArrowUp, ArrowDown, ArrowUpDown } from 'lucide-react'
import { clsx } from 'clsx'
import { type WCPlayerRanking, getWCRankings } from '@/lib/api'
import { FlagImg } from '@/components/FlagImg'

type SortKey = 'goals' | 'assists' | 'xg' | 'xa' | 'finishing_delta' | 'creation_delta' | 'xg_per_90' | 'xa_per_90' | 'minutes' | 'shots'
type PosFilter = '' | 'GK' | 'DEF' | 'MID' | 'FWD'

interface SortState {
  key: SortKey
  dir: 'asc' | 'desc'
}

function DeltaCell({ value }: { value: number }) {
  const fmt = value > 0 ? `+${value.toFixed(2)}` : value.toFixed(2)
  return (
    <span className={clsx(
      'font-mono text-xs',
      value > 0.1  ? 'text-green-400' :
      value < -0.1 ? 'text-red-400'   : 'text-gray-500',
    )}>
      {fmt}
    </span>
  )
}

function SortIcon({ col, sort }: { col: SortKey; sort: SortState }) {
  if (sort.key !== col) return <ArrowUpDown className="w-3 h-3 text-gray-600" />
  return sort.dir === 'desc'
    ? <ArrowDown className="w-3 h-3 text-orange-400" />
    : <ArrowUp className="w-3 h-3 text-orange-400" />
}

function Th({
  col, label, sort, onSort, align = 'right',
}: {
  col: SortKey
  label: string
  sort: SortState
  onSort: (k: SortKey) => void
  align?: 'left' | 'right'
}) {
  return (
    <th
      className={clsx(
        'px-3 py-2 text-[10px] font-semibold uppercase tracking-wider cursor-pointer select-none whitespace-nowrap',
        'hover:text-white transition-colors',
        sort.key === col ? 'text-orange-400' : 'text-gray-500',
        align === 'right' ? 'text-right' : 'text-left',
      )}
      onClick={() => onSort(col)}
    >
      <span className="inline-flex items-center gap-1">
        {align === 'right' && <SortIcon col={col} sort={sort} />}
        {label}
        {align === 'left' && <SortIcon col={col} sort={sort} />}
      </span>
    </th>
  )
}

const POS_ORDER = ['GK', 'DEF', 'MID', 'FWD'] as const

export default function WC2026StatsPage() {
  const [rows, setRows] = useState<WCPlayerRanking[]>([])
  const [loading, setLoading] = useState(true)
  const [sort, setSort] = useState<SortState>({ key: 'xg', dir: 'desc' })
  const [posFilter, setPosFilter] = useState<PosFilter>('')
  const [nationFilter, setNationFilter] = useState('')
  const [search, setSearch] = useState('')

  async function load() {
    setLoading(true)
    try {
      setRows(await getWCRankings())
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  function toggleSort(key: SortKey) {
    setSort((prev) =>
      prev.key === key
        ? { key, dir: prev.dir === 'desc' ? 'asc' : 'desc' }
        : { key, dir: 'desc' },
    )
  }

  const nations = useMemo(
    () => [...new Set(rows.map((r) => r.nation).filter(Boolean) as string[])].sort((a, b) => a.localeCompare(b, 'fr')),
    [rows],
  )

  const filtered = useMemo(() => {
    let out = rows
    if (posFilter)    out = out.filter((r) => r.position === posFilter)
    if (nationFilter) out = out.filter((r) => r.nation === nationFilter)
    if (search)       out = out.filter((r) => r.player_name.toLowerCase().includes(search.toLowerCase()))
    return [...out].sort((a, b) => {
      const av = (a[sort.key] ?? -Infinity) as number
      const bv = (b[sort.key] ?? -Infinity) as number
      return sort.dir === 'desc' ? bv - av : av - bv
    })
  }, [rows, posFilter, nationFilter, search, sort])

  return (
    <div className="p-4 space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-3 flex-wrap">
          <h1 className="text-sm font-semibold text-white">Classement CDM 2026</h1>

          {/* Filtre position */}
          <div className="flex gap-1">
            {(['', ...POS_ORDER] as PosFilter[]).map((pos) => (
              <button
                key={pos || 'all'}
                onClick={() => setPosFilter(pos)}
                className={clsx(
                  'px-2 py-0.5 text-[10px] rounded border transition-colors',
                  posFilter === pos
                    ? 'bg-orange-500/20 border-orange-500/50 text-orange-300'
                    : 'border-gray-600 text-gray-400 hover:text-white',
                )}
              >
                {pos || 'Tous'}
              </button>
            ))}
          </div>

          {/* Filtre nation */}
          <select
            value={nationFilter}
            onChange={(e) => setNationFilter(e.target.value)}
            className="px-2 py-0.5 text-xs bg-gray-800 border border-gray-600 rounded text-white"
          >
            <option value="">Toutes les nations</option>
            {nations.map((n) => <option key={n} value={n}>{n}</option>)}
          </select>

          {/* Recherche joueur */}
          <input
            type="text"
            placeholder="Joueur…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-32 px-2 py-0.5 text-xs bg-gray-800 border border-gray-600 rounded text-white placeholder-gray-500"
          />
        </div>

        <div className="flex items-center gap-2">
          <span className="text-[10px] text-gray-500">{filtered.length} joueurs</span>
          <button
            onClick={load}
            disabled={loading}
            className="p-1.5 rounded hover:bg-gray-700 text-gray-400 hover:text-white transition-colors disabled:opacity-40"
          >
            <RefreshCw className={clsx('w-3.5 h-3.5', loading && 'animate-spin')} />
          </button>
        </div>
      </div>

      {/* Table */}
      <div className="overflow-x-auto rounded-lg border border-gray-700">
        <table className="w-full text-xs text-gray-300">
          <thead className="bg-gray-800/60 border-b border-gray-700">
            <tr>
              <th className="px-3 py-2 text-left text-[10px] font-semibold uppercase tracking-wider text-gray-500 w-6">#</th>
              <th className="px-3 py-2 text-left text-[10px] font-semibold uppercase tracking-wider text-gray-500">Joueur</th>
              <th className="px-3 py-2 text-left text-[10px] font-semibold uppercase tracking-wider text-gray-500">Nation</th>
              <th className="px-3 py-2 text-center text-[10px] font-semibold uppercase tracking-wider text-gray-500">Pos</th>
              <Th col="minutes"          label="Min"    sort={sort} onSort={toggleSort} />
              <Th col="goals"            label="Buts"   sort={sort} onSort={toggleSort} />
              <Th col="xg"              label="xG"     sort={sort} onSort={toggleSort} />
              <Th col="finishing_delta" label="Δ Buts"  sort={sort} onSort={toggleSort} />
              <Th col="assists"         label="Passes"  sort={sort} onSort={toggleSort} />
              <Th col="xa"              label="xA"     sort={sort} onSort={toggleSort} />
              <Th col="creation_delta"  label="Δ xA"   sort={sort} onSort={toggleSort} />
              <Th col="shots"           label="Tirs"   sort={sort} onSort={toggleSort} />
              <Th col="xg_per_90"       label="xG/90"  sort={sort} onSort={toggleSort} />
              <Th col="xa_per_90"       label="xA/90"  sort={sort} onSort={toggleSort} />
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-800">
            {loading && (
              <tr>
                <td colSpan={14} className="py-12 text-center text-gray-500 text-sm">
                  Chargement…
                </td>
              </tr>
            )}
            {!loading && filtered.length === 0 && (
              <tr>
                <td colSpan={14} className="py-12 text-center text-gray-500 text-sm">
                  Aucun joueur — synchronise des matchs dans l'onglet Matchs
                </td>
              </tr>
            )}
            {!loading && filtered.map((r, i) => (
              <tr
                key={r.player_name + (r.nation ?? '')}
                className="hover:bg-gray-800/40 transition-colors"
              >
                <td className="px-3 py-2 text-gray-600 text-[10px]">{i + 1}</td>
                <td className="px-3 py-2 font-medium text-white whitespace-nowrap">{r.player_name}</td>
                <td className="px-3 py-2 whitespace-nowrap">
                  {r.nation ? (
                    <span className="flex items-center gap-1">
                      {r.flag_emoji && <FlagImg emoji={r.flag_emoji} size={14} />}
                      <span className="text-gray-300 text-[11px]">{r.nation}</span>
                    </span>
                  ) : (
                    <span className="text-gray-600 text-[10px]">—</span>
                  )}
                </td>
                <td className="px-3 py-2 text-center">
                  {r.position ? (
                    <span className={clsx(
                      'text-[9px] font-bold px-1.5 py-0.5 rounded uppercase',
                      r.position === 'GK'  ? 'bg-yellow-900/40 text-yellow-400' :
                      r.position === 'DEF' ? 'bg-blue-900/40  text-blue-400'  :
                      r.position === 'MID' ? 'bg-green-900/40 text-green-400' :
                                             'bg-red-900/40   text-red-400',
                    )}>
                      {r.position}
                    </span>
                  ) : <span className="text-gray-600">—</span>}
                </td>
                <td className="px-3 py-2 text-right font-mono text-gray-400">{r.minutes}</td>
                <td className="px-3 py-2 text-right font-mono font-semibold text-white">{r.goals}</td>
                <td className="px-3 py-2 text-right font-mono text-gray-300">{r.xg.toFixed(2)}</td>
                <td className="px-3 py-2 text-right"><DeltaCell value={r.finishing_delta} /></td>
                <td className="px-3 py-2 text-right font-mono font-semibold text-white">{r.assists}</td>
                <td className="px-3 py-2 text-right font-mono text-gray-300">{r.xa.toFixed(2)}</td>
                <td className="px-3 py-2 text-right"><DeltaCell value={r.creation_delta} /></td>
                <td className="px-3 py-2 text-right font-mono text-gray-400">{r.shots}</td>
                <td className="px-3 py-2 text-right font-mono text-gray-400">
                  {r.xg_per_90 != null ? r.xg_per_90.toFixed(2) : '—'}
                </td>
                <td className="px-3 py-2 text-right font-mono text-gray-400">
                  {r.xa_per_90 != null ? r.xa_per_90.toFixed(2) : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Légende delta */}
      <p className="text-[10px] text-gray-600">
        Δ Buts = Buts réels − xG &nbsp;·&nbsp; Δ xA = Passes réelles − xA &nbsp;·&nbsp;
        <span className="text-green-600">vert</span> = surperformance &nbsp;·&nbsp;
        <span className="text-red-600">rouge</span> = sous-performance
      </p>
    </div>
  )
}
