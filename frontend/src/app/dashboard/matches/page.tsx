'use client'

import { useCallback, useEffect, useState } from 'react'
import Link from 'next/link'
import { clsx } from 'clsx'
import { AlertCircle, RefreshCw } from 'lucide-react'

/** Championnats du périmètre. La Ligue des champions est absente : elle attend
 *  les tirages de la phase de ligue. */
const LEAGUES: { id: number | null; label: string; flag: string }[] = [
  { id: null, label: 'Tous', flag: '' },
  { id: 1, label: 'Premier League', flag: '🏴󠁧󠁢󠁥󠁮󠁧󠁿' },
  { id: 3, label: 'La Liga', flag: '🇪🇸' },
  { id: 4, label: 'Serie A', flag: '🇮🇹' },
  { id: 5, label: 'Bundesliga', flag: '🇩🇪' },
  { id: 6, label: 'Ligue 1', flag: '🇫🇷' },
]

interface MatchListItem {
  event_api_id: number
  event_date: string | null
  status: string | null
  league_api_id: number | null
  home_team: string
  away_team: string
  home_score: number | null
  away_score: number | null
  a_des_donnees: boolean
}

function fmtDate(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('fr-FR', {
    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
  })
}

export default function MatchesPage() {
  const [matches, setMatches] = useState<MatchListItem[]>([])
  const [league, setLeague] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [erreur, setErreur] = useState<string | null>(null)

  const charger = useCallback(async () => {
    setLoading(true)
    setErreur(null)
    try {
      const params = new URLSearchParams({ limit: '100' })
      if (league !== null) params.set('league_api_id', league.toString())
      const r = await fetch(`/api/v1/matches?${params}`)
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      setMatches(await r.json())
    } catch (e) {
      setErreur(`Impossible de charger les matchs : ${(e as Error).message}`)
      setMatches([])
    } finally {
      setLoading(false)
    }
  }, [league])

  useEffect(() => { charger() }, [charger])

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <h1 className="text-xl font-semibold text-white">Matchs</h1>
        <button
          onClick={charger}
          className="inline-flex items-center gap-2 px-3 py-2 text-sm bg-gray-800 border border-gray-700 rounded-lg text-gray-300 hover:text-white"
        >
          <RefreshCw className="w-4 h-4" /> Actualiser
        </button>
      </div>

      <div className="flex flex-wrap gap-2">
        {LEAGUES.map(({ id, label, flag }) => (
          <button
            key={id ?? 'all'}
            onClick={() => setLeague(id)}
            className={clsx(
              'px-3 py-1.5 rounded-full text-sm border transition-colors',
              league === id
                ? 'bg-brand-600 border-brand-600 text-white font-medium'
                : 'bg-gray-800 border-gray-700 text-gray-400 hover:text-white',
            )}
          >
            {flag} {label}
          </button>
        ))}
      </div>

      {erreur && (
        <div className="flex items-center gap-2 p-3 rounded-lg bg-red-500/10 border border-red-500/30 text-red-300 text-sm">
          <AlertCircle className="w-4 h-4 shrink-0" /> {erreur}
        </div>
      )}

      <div className="bg-gray-800 rounded-xl overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-gray-400 border-b border-gray-700">
                <th className="text-left px-4 py-2 font-medium">Date</th>
                <th className="text-left px-3 py-2 font-medium">Match</th>
                <th className="px-3 py-2 font-medium">Score</th>
                <th className="px-3 py-2 font-medium">Données</th>
              </tr>
            </thead>
            <tbody>
              {loading && (
                <tr><td colSpan={4} className="px-4 py-8 text-center text-gray-500">Chargement…</td></tr>
              )}
              {!loading && matches.length === 0 && (
                <tr><td colSpan={4} className="px-4 py-8 text-center text-gray-500">Aucun match</td></tr>
              )}
              {matches.map((m) => (
                <tr key={m.event_api_id} className="border-b border-gray-700/50 hover:bg-ev-surface2">
                  <td className="px-4 py-2 text-gray-400 whitespace-nowrap">{fmtDate(m.event_date)}</td>
                  <td className="px-3 py-2">
                    <Link href={`/dashboard/matches/${m.event_api_id}`} className="text-white hover:text-brand-400">
                      {m.home_team} — {m.away_team}
                    </Link>
                  </td>
                  <td className="px-3 py-2 text-center font-mono text-white">
                    {m.home_score != null && m.away_score != null
                      ? `${m.home_score} - ${m.away_score}`
                      : <span className="text-gray-600">—</span>}
                  </td>
                  <td className="px-3 py-2 text-center">
                    {/* Un match sans carte des tirs n'a pas de fiche exploitable.
                        Le signaler ici évite d'ouvrir une page vide. */}
                    {m.a_des_donnees
                      ? <span className="text-emerald-400 text-xs">complet</span>
                      : <span className="text-gray-600 text-xs">à venir</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
