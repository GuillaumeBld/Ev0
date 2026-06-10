'use client'

import { useState, useEffect, useCallback } from 'react'
import { RefreshCw } from 'lucide-react'
import { clsx } from 'clsx'
import {
  type WCPlayerPricing,
  type WCNationStatus,
  computeWCPricing,
  getWCPricingPlayers,
  getWCLineupNations,
} from '@/lib/api'
import { PricingTable } from '@/components/wc2026/PricingTable'

type Tab = 'goals' | 'assists'
type PosFilter = '' | 'FW' | 'MF' | 'DF'

export default function WC2026PricingPage() {
  const [players, setPlayers] = useState<WCPlayerPricing[]>([])
  const [nations, setNations] = useState<WCNationStatus[]>([])
  const [loading, setLoading] = useState(true)
  const [computing, setComputing] = useState(false)
  const [computeMsg, setComputeMsg] = useState<string | null>(null)
  const [tab, setTab] = useState<Tab>('goals')
  const [nationFilter, setNationFilter] = useState('')
  const [posFilter, setPosFilter] = useState<PosFilter>('')
  const [minLambda, setMinLambda] = useState('')

  const loadPlayers = useCallback(async () => {
    setLoading(true)
    try {
      const data = await getWCPricingPlayers({
        ...(nationFilter ? { nation: nationFilter } : {}),
        ...(posFilter     ? { position: posFilter }  : {}),
        ...(minLambda && !isNaN(parseFloat(minLambda))
          ? { min_lambda: parseFloat(minLambda) } : {}),
      })
      setPlayers(data)
    } finally {
      setLoading(false)
    }
  }, [nationFilter, posFilter, minLambda])

  useEffect(() => {
    loadNations()
    loadPlayers()
  }, [loadPlayers])

  async function loadNations() {
    const data = await getWCLineupNations()
    setNations(data)
  }

  async function handleCompute() {
    setComputing(true)
    setComputeMsg(null)
    try {
      const res = await computeWCPricing()
      setComputeMsg(`${res.players_computed} joueurs calculés (${res.duration_s}s)`)
      await loadPlayers()
    } catch {
      setComputeMsg('Erreur de calcul')
    } finally {
      setComputing(false)
    }
  }

  const nationFlags = Object.fromEntries(
    nations.map((n) => [n.nation, n.flag_emoji])
  )

  const displayed = [...players].sort((a, b) =>
    tab === 'goals'
      ? b.lambda_goals - a.lambda_goals
      : b.lambda_assists - a.lambda_assists
  )

  return (
    <div className="p-4 flex flex-col h-full gap-4">
      {/* Header */}
      <div className="flex items-center gap-3 flex-wrap">
        <h2 className="text-sm font-semibold text-white">Pricing CDM 2026</h2>

        {/* Nation filter */}
        <select
          value={nationFilter}
          onChange={(e) => setNationFilter(e.target.value)}
          className="px-2 py-1 text-xs bg-gray-800 border border-gray-600 rounded text-white"
        >
          <option value="">Toutes les nations</option>
          {nations.map((n) => (
            <option key={n.nation} value={n.nation}>{n.nation}</option>
          ))}
        </select>

        {/* Position filter */}
        <div className="flex gap-1">
          {(['', 'FW', 'MF', 'DF'] as PosFilter[]).map((pos) => (
            <button
              key={pos || 'all'}
              onClick={() => setPosFilter(pos)}
              className={clsx(
                'px-2 py-1 text-xs rounded border transition-colors',
                posFilter === pos
                  ? 'bg-orange-500/20 border-orange-500/50 text-orange-300'
                  : 'border-gray-600 text-gray-400 hover:text-white',
              )}
            >
              {pos || 'Tous'}
            </button>
          ))}
        </div>

        {/* Lambda filter */}
        <input
          type="number"
          placeholder="λ min"
          value={minLambda}
          onChange={(e) => setMinLambda(e.target.value)}
          className="w-20 px-2 py-1 text-xs bg-gray-800 border border-gray-600 rounded text-white"
        />

        <div className="ml-auto flex items-center gap-2">
          {computeMsg && <span className="text-xs text-gray-400">{computeMsg}</span>}
          <button
            onClick={handleCompute}
            disabled={computing}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-orange-500 hover:bg-orange-600 disabled:opacity-40 text-white text-xs font-medium rounded-lg transition-colors"
          >
            <RefreshCw className={clsx('w-3 h-3', computing && 'animate-spin')} />
            {computing ? 'Calcul…' : 'Recalculer'}
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 border-b border-gray-700 pb-0">
        {(['goals', 'assists'] as Tab[]).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={clsx(
              'px-4 py-2 text-xs font-medium border-b-2 transition-colors -mb-px',
              tab === t
                ? 'border-orange-500 text-orange-300'
                : 'border-transparent text-gray-400 hover:text-white',
            )}
          >
            {t === 'goals' ? 'Buts' : 'Passes'}
          </button>
        ))}
      </div>

      {/* Table */}
      <div className="flex-1 overflow-y-auto">
        {loading ? (
          <p className="text-gray-500 text-sm p-4">Chargement…</p>
        ) : (
          <PricingTable players={displayed} mode={tab} nationFlags={nationFlags} />
        )}
      </div>
    </div>
  )
}
