'use client'

import { useState, useEffect } from 'react'
import { RefreshCw } from 'lucide-react'
import { clsx } from 'clsx'
import { getWCAdvancement, type WCTeamAdvancement } from '@/lib/api'

const NATION_FLAGS: Record<string, string> = {
  'Spain': '🇪🇸', 'Brazil': '🇧🇷', 'Germany': '🇩🇪', 'England': '🏴󠁧󠁢󠁥󠁮󠁧󠁿',
  'France': '🇫🇷', 'Argentina': '🇦🇷', 'Portugal': '🇵🇹', 'Belgium': '🇧🇪',
  'Netherlands': '🇳🇱', 'Switzerland': '🇨🇭', 'Colombia': '🇨🇴', 'Norway': '🇳🇴',
  'Mexico': '🇲🇽', 'Ecuador': '🇪🇨', 'Uruguay': '🇺🇾', 'Canada': '🇨🇦',
  'United States': '🇺🇸', 'Croatia': '🇭🇷', 'Morocco': '🇲🇦', 'Ivory Coast': '🇨🇮',
  'Austria': '🇦🇹', 'Turkey': '🇹🇷', 'Japan': '🇯🇵', 'Senegal': '🇸🇳',
  'Egypt': '🇪🇬', 'Scotland': '🏴󠁧󠁢󠁳󠁣󠁴󠁿', 'South Korea': '🇰🇷', 'Czechia': '🇨🇿',
  'Sweden': '🇸🇪', 'Bosnia-Herzegovina': '🇧🇦', 'Algeria': '🇩🇿', 'Paraguay': '🇵🇾',
  'Iran': '🇮🇷', 'Ghana': '🇬🇭', 'Australia': '🇦🇺', 'Congo DR': '🇨🇩',
  'Panama': '🇵🇦', 'New Zealand': '🇳🇿', 'South Africa': '🇿🇦', 'Uzbekistan': '🇺🇿',
  'Tunisia': '🇹🇳', 'Cape Verde Islands': '🇨🇻', 'Saudi Arabia': '🇸🇦',
  'Curaçao': '🇨🇼', 'Haiti': '🇭🇹', 'Jordan': '🇯🇴', 'Qatar': '🇶🇦', 'Iraq': '🇮🇶',
}

function ProbBar({ value, color }: { value: number; color: string }) {
  const pct = Math.round(value * 100)
  if (pct === 0) return <span className="text-gray-600 tabular-nums">—</span>
  return (
    <div className="flex items-center gap-1.5 min-w-[56px]">
      <div className="flex-1 h-1.5 bg-gray-800 rounded-full overflow-hidden">
        <div className={clsx('h-full rounded-full', color)} style={{ width: `${Math.min(pct, 100)}%` }} />
      </div>
      <span className="text-xs tabular-nums w-8 text-right">{pct}%</span>
    </div>
  )
}

type SortKey = 'elo' | 'p_r32' | 'p_r16' | 'p_qf' | 'p_sf' | 'p_finalist' | 'p_winner' | 'e_games'

export default function WC2026BracketPage() {
  const [teams, setTeams] = useState<WCTeamAdvancement[]>([])
  const [loading, setLoading] = useState(true)
  const [sortKey, setSortKey] = useState<SortKey>('elo')
  const [computedAt, setComputedAt] = useState<string | null>(null)

  async function load() {
    setLoading(true)
    try {
      const data = await getWCAdvancement()
      setTeams(data)
      if (data.length > 0) setComputedAt(data[0].computed_at)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const sorted = [...teams].sort((a, b) => b[sortKey] - a[sortKey])

  function Th({ label, k, className }: { label: string; k: SortKey; className?: string }) {
    const active = sortKey === k
    return (
      <th
        className={clsx(
          'px-3 py-2 text-right text-[10px] font-semibold uppercase tracking-wider cursor-pointer select-none whitespace-nowrap',
          active ? 'text-orange-400' : 'text-gray-500 hover:text-gray-300',
          className,
        )}
        onClick={() => setSortKey(k)}
      >
        {label}{active && ' ↓'}
      </th>
    )
  }

  const formattedAt = computedAt
    ? new Date(computedAt).toLocaleString('fr-FR', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })
    : null

  return (
    <div className="p-4 flex flex-col h-full gap-4">
      {/* Header */}
      <div className="flex items-center gap-3">
        <h2 className="text-sm font-semibold text-white">Bracket CDM 2026 — ELO & probabilités</h2>
        {formattedAt && (
          <span className="text-[10px] text-gray-500">calculé le {formattedAt}</span>
        )}
        <button
          onClick={load}
          disabled={loading}
          className="ml-auto flex items-center gap-1.5 px-2.5 py-1.5 bg-gray-700 hover:bg-gray-600 disabled:opacity-40 text-white text-xs rounded-lg transition-colors"
        >
          <RefreshCw className={clsx('w-3 h-3', loading && 'animate-spin')} />
          Rafraîchir
        </button>
      </div>

      {/* Table */}
      <div className="flex-1 overflow-auto">
        {loading ? (
          <p className="text-gray-500 text-sm p-4">Chargement…</p>
        ) : teams.length === 0 ? (
          <p className="text-gray-500 text-sm p-4">Aucune donnée — le job bracket n'a pas encore tourné.</p>
        ) : (
          <table className="w-full text-xs border-collapse">
            <thead className="sticky top-0 bg-gray-900 z-10">
              <tr className="border-b border-gray-700">
                <th className="px-3 py-2 text-left text-[10px] font-semibold text-gray-500 uppercase tracking-wider w-6">#</th>
                <th className="px-3 py-2 text-left text-[10px] font-semibold text-gray-500 uppercase tracking-wider">Nation</th>
                <Th label="ELO" k="elo" className="text-left" />
                <Th label="Groupes" k="p_r32" />
                <Th label="R32" k="p_r16" />
                <Th label="R16" k="p_qf" />
                <Th label="QF" k="p_sf" />
                <Th label="SF" k="p_finalist" />
                <Th label="Finale" k="p_winner" />
                <Th label="E[matchs]" k="e_games" />
              </tr>
            </thead>
            <tbody>
              {sorted.map((t, i) => (
                <tr
                  key={t.nation}
                  className="border-b border-gray-800/60 hover:bg-gray-800/40 transition-colors"
                >
                  <td className="px-3 py-2 text-gray-600 tabular-nums">{i + 1}</td>
                  <td className="px-3 py-2">
                    <div className="flex items-center gap-2">
                      <span className="text-base leading-none">{NATION_FLAGS[t.nation] ?? '🏳'}</span>
                      <span className="text-gray-200 font-medium">{t.nation}</span>
                    </div>
                  </td>
                  <td className="px-3 py-2 text-left">
                    <span className={clsx(
                      'font-mono font-semibold tabular-nums',
                      t.elo >= 1650 ? 'text-emerald-400' :
                      t.elo >= 1550 ? 'text-yellow-400' :
                      t.elo >= 1450 ? 'text-orange-400' : 'text-gray-400'
                    )}>
                      {Math.round(t.elo)}
                    </span>
                  </td>
                  <td className="px-3 py-2"><ProbBar value={t.p_r32} color="bg-blue-500" /></td>
                  <td className="px-3 py-2"><ProbBar value={t.p_r16} color="bg-cyan-500" /></td>
                  <td className="px-3 py-2"><ProbBar value={t.p_qf} color="bg-teal-500" /></td>
                  <td className="px-3 py-2"><ProbBar value={t.p_sf} color="bg-yellow-500" /></td>
                  <td className="px-3 py-2"><ProbBar value={t.p_finalist} color="bg-orange-500" /></td>
                  <td className="px-3 py-2"><ProbBar value={t.p_winner} color="bg-emerald-500" /></td>
                  <td className="px-3 py-2 text-right">
                    <span className="tabular-nums text-gray-300">{t.e_games.toFixed(2)}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
