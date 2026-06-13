// frontend/src/app/dashboard/wc2026/lineups/page.tsx
'use client'

import { useState, useEffect } from 'react'
import { RefreshCw, CheckCircle, Circle } from 'lucide-react'
import { clsx } from 'clsx'
import {
  type WCNationStatus,
  type WCNationLineups,
  getWCLineupNations,
  getWCNationLineups,
  syncRotowireLineups,
  syncBzzoiroLineups,
  fillSubsLineups,
} from '@/lib/api'
import { LineupPitchEditor } from '@/components/wc2026/LineupPitchEditor'
import { FlagImg } from '@/components/FlagImg'

export default function WC2026LineupsPage() {
  const [nations, setNations] = useState<WCNationStatus[]>([])
  const [selectedNation, setSelectedNation] = useState<string | null>(null)
  const [nationData, setNationData] = useState<WCNationLineups | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadingNation, setLoadingNation] = useState(false)
  const [syncing, setSyncing] = useState(false)
  const [syncingBzz, setSyncingBzz] = useState(false)
  const [fillingBench, setFillingBench] = useState(false)
  const [syncMsg, setSyncMsg] = useState<string | null>(null)

  async function loadNations() {
    try {
      const data = await getWCLineupNations()
      setNations(data)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { loadNations() }, [])

  async function selectNation(nation: string) {
    setSelectedNation(nation)
    setLoadingNation(true)
    try {
      const data = await getWCNationLineups(nation)
      setNationData(data)
    } finally {
      setLoadingNation(false)
    }
  }

  async function handleSyncRotowire() {
    setSyncing(true)
    setSyncMsg(null)
    try {
      const res = await syncRotowireLineups()
      setSyncMsg(`Rotowire : ${res.seeded} seedées, ${res.skipped_manual} manuelles ignorées, ${res.no_match} non trouvées`)
      await loadNations()
    } catch {
      setSyncMsg('Erreur de sync Rotowire')
    } finally {
      setSyncing(false)
    }
  }

  async function handleFillBench() {
    setFillingBench(true)
    setSyncMsg(null)
    try {
      const res = await fillSubsLineups()
      setSyncMsg(`Bancs remplis : ${res.added_total} joueurs ajoutés sur ${Object.keys(res.by_nation).length} nations`)
      await loadNations()
      if (selectedNation) await selectNation(selectedNation)
    } catch {
      setSyncMsg('Erreur fill-subs')
    } finally {
      setFillingBench(false)
    }
  }

  async function handleSyncBzzoiro() {
    setSyncingBzz(true)
    setSyncMsg(null)
    try {
      const res = await syncBzzoiroLineups()
      setSyncMsg(`Bzzoiro : ${res.synced} compos mises à jour (${res.total_matches} matchs scannés)`)
      await loadNations()
      if (selectedNation) await selectNation(selectedNation)
    } catch {
      setSyncMsg('Erreur de sync Bzzoiro')
    } finally {
      setSyncingBzz(false)
    }
  }

  const byGroup: Record<string, WCNationStatus[]> = {}
  for (const n of nations) {
    ;(byGroup[n.group_letter] ??= []).push(n)
  }

  if (loading) {
    return <div className="p-6 text-gray-400 text-sm">Chargement…</div>
  }

  return (
    <div className="flex h-[calc(100vh-4rem)] overflow-hidden">
      {/* Nation list sidebar */}
      <div className="w-56 shrink-0 border-r border-gray-700 overflow-y-auto p-3">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-semibold text-white">CDM 2026</h2>
          <div className="flex items-center gap-1">
            <button
              onClick={handleFillBench}
              disabled={fillingBench}
              title="Remplir les bancs depuis la squad (ATT/MIL 30min, DEF 10min)"
              className="px-2 py-1 text-[10px] rounded hover:bg-purple-900/40 text-purple-400 hover:text-purple-300 border border-purple-800/50 hover:border-purple-600 transition-colors disabled:opacity-40 flex items-center gap-1"
            >
              <RefreshCw className={clsx('w-3 h-3', fillingBench && 'animate-spin')} />
              Bancs
            </button>
            <button
              onClick={handleSyncBzzoiro}
              disabled={syncingBzz}
              title="Sync Bzzoiro (compos matchs terminés)"
              className="px-2 py-1 text-[10px] rounded hover:bg-blue-900/40 text-blue-400 hover:text-blue-300 border border-blue-800/50 hover:border-blue-600 transition-colors disabled:opacity-40 flex items-center gap-1"
            >
              <RefreshCw className={clsx('w-3 h-3', syncingBzz && 'animate-spin')} />
              Bzz
            </button>
            <button
              onClick={handleSyncRotowire}
              disabled={syncing}
              title="Sync Rotowire"
              className="p-1.5 rounded hover:bg-gray-700 text-gray-400 hover:text-white transition-colors disabled:opacity-40"
            >
              <RefreshCw className={clsx('w-3.5 h-3.5', syncing && 'animate-spin')} />
            </button>
          </div>
        </div>

        {syncMsg && (
          <p className="text-[10px] text-gray-500 mb-2 leading-tight">{syncMsg}</p>
        )}

        {Object.entries(byGroup).sort().map(([group, groupNations]) => (
          <div key={group} className="mb-3">
            <div className="text-[10px] font-bold text-gray-500 uppercase tracking-wider mb-1">
              Groupe {group}
            </div>
            {groupNations.map((n) => (
              <button
                key={n.nation}
                onClick={() => selectNation(n.nation)}
                className={clsx(
                  'w-full flex items-center gap-2 px-2 py-1.5 rounded text-xs transition-colors text-left',
                  selectedNation === n.nation
                    ? 'bg-orange-500/20 text-orange-200'
                    : 'text-gray-300 hover:bg-gray-800',
                )}
              >
                {n.complete ? (
                  <CheckCircle className="w-3 h-3 text-green-400 shrink-0" />
                ) : (
                  <Circle className="w-3 h-3 text-gray-600 shrink-0" />
                )}
                <span className="truncate flex items-center gap-1">
                  <FlagImg emoji={n.flag_emoji} size={16} />
                  {n.nation}
                </span>
                {!n.complete && n.starters_count > 0 && (
                  <span className="ml-auto text-[10px] text-gray-500">{n.starters_count}/11</span>
                )}
              </button>
            ))}
          </div>
        ))}
      </div>

      {/* Editor panel */}
      <div className="flex-1 overflow-y-auto p-4">
        {!selectedNation && (
          <div className="flex items-center justify-center h-full text-gray-500 text-sm">
            Sélectionne une nation pour éditer sa composition
          </div>
        )}

        {selectedNation && loadingNation && (
          <div className="flex items-center justify-center h-full text-gray-400 text-sm">
            Chargement…
          </div>
        )}

        {selectedNation && !loadingNation && nationData && (
          <>
            <h3 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
              <FlagImg emoji={nationData.flag_emoji} size={24} />
              {selectedNation}
            </h3>
            <LineupPitchEditor
              nation={selectedNation}
              flagEmoji={nationData.flag_emoji}
              squad={nationData.squad}
              initialLineups={nationData.lineups}
              onSaved={async () => {
                await loadNations()
                if (selectedNation) await selectNation(selectedNation)
              }}
            />
          </>
        )}
      </div>
    </div>
  )
}
