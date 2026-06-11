'use client'

import { useState, useEffect, useCallback } from 'react'
import { RefreshCw } from 'lucide-react'
import { clsx } from 'clsx'
import {
  type WCPlayerPricing,
  type WCNationStatus,
  type WCNationOdds,
  computeWCPricing,
  getWCPricingPlayers,
  getWCLineupNations,
  getWCNationsOdds,
} from '@/lib/api'
import { PricingTable } from '@/components/wc2026/PricingTable'
import { NationsOddsTable } from '@/components/wc2026/NationsOddsTable'

type Tab = 'goals' | 'assists' | 'nations'
type PosFilter = '' | 'FW' | 'MF' | 'DF'

export default function WC2026PricingPage() {
  const [players, setPlayers] = useState<WCPlayerPricing[]>([])
  const [nations, setNations] = useState<WCNationStatus[]>([])
  const [nationOdds, setNationOdds] = useState<WCNationOdds[]>([])
  const [loading, setLoading] = useState(true)
  const [loadingNations, setLoadingNations] = useState(false)
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

  async function loadNationOdds() {
    setLoadingNations(true)
    try {
      const data = await getWCNationsOdds()
      setNationOdds(data)
    } finally {
      setLoadingNations(false)
    }
  }

  useEffect(() => {
    getWCLineupNations().then(setNations)
    loadPlayers()
    loadNationOdds()
  }, [loadPlayers])

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

  const isPlayerTab = tab === 'goals' || tab === 'assists'

  return (
    <div className="p-4 flex flex-col h-full gap-4">
      {/* Header */}
      <div className="flex items-center gap-3 flex-wrap">
        <h2 className="text-sm font-semibold text-white">Pricing CDM 2026</h2>

        {/* Filters — only for player tabs */}
        {isPlayerTab && (
          <>
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

            <input
              type="number"
              placeholder="λ min"
              value={minLambda}
              onChange={(e) => setMinLambda(e.target.value)}
              className="w-20 px-2 py-1 text-xs bg-gray-800 border border-gray-600 rounded text-white"
            />
          </>
        )}

        <div className="ml-auto flex items-center gap-2">
          {computeMsg && <span className="text-xs text-gray-400">{computeMsg}</span>}
          {isPlayerTab && (
            <button
              onClick={handleCompute}
              disabled={computing}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-orange-500 hover:bg-orange-600 disabled:opacity-40 text-white text-xs font-medium rounded-lg transition-colors"
            >
              <RefreshCw className={clsx('w-3 h-3', computing && 'animate-spin')} />
              {computing ? 'Calcul…' : 'Recalculer'}
            </button>
          )}
          {tab === 'nations' && (
            <button
              onClick={loadNationOdds}
              disabled={loadingNations}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-gray-700 hover:bg-gray-600 disabled:opacity-40 text-white text-xs font-medium rounded-lg transition-colors"
            >
              <RefreshCw className={clsx('w-3 h-3', loadingNations && 'animate-spin')} />
              Rafraîchir
            </button>
          )}
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 border-b border-gray-700 pb-0">
        {(['goals', 'assists', 'nations'] as Tab[]).map((t) => (
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
            {t === 'goals' ? 'Buts' : t === 'assists' ? 'Passes' : 'Nations'}
          </button>
        ))}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto">
        {tab === 'nations' ? (
          loadingNations ? (
            <p className="text-gray-500 text-sm p-4">Chargement…</p>
          ) : (
            <NationsOddsTable nations={nationOdds} />
          )
        ) : loading ? (
          <p className="text-gray-500 text-sm p-4">Chargement…</p>
        ) : (
          <PricingTable players={players} mode={tab} nationFlags={nationFlags} />
        )}
      </div>
    </div>
  )
}
