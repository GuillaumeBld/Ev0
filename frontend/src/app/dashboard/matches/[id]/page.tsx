'use client'

import { useEffect, useState } from 'react'
import { useParams } from 'next/navigation'
import Link from 'next/link'
import { clsx } from 'clsx'
import { AlertCircle, ArrowLeft } from 'lucide-react'
import { ShotMap } from '@/components/wc2026/ShotMap'
import { type WCShotPoint } from '@/lib/api'

interface CompoJoueur {
  player_name: string
  position: string | null
  is_starter: boolean
  jersey_number: number | null
}

interface Compo {
  team: string
  lineup_type: string
  lineup_status: string | null
  published_at: string | null
  players: CompoJoueur[]
}

interface FicheMatch {
  event_api_id: number
  event_date: string | null
  status: string | null
  home_team: string
  away_team: string
  home_score: number | null
  away_score: number | null
  home_xg: number | null
  away_xg: number | null
  shotmap: WCShotPoint[]
  incidents: Record<string, unknown>[]
  home_lineup: Compo | null
  away_lineup: Compo | null
  player_stats: Record<string, unknown>[]
  blocs_manquants: string[]
}

/** Ce que vaut une compo, dit en clair.
 *  Pricer sur la dernière compo connue d'une équipe n'est pas pricer sur la
 *  compo du jour, et cela doit se voir. */
function origineCompo(c: Compo): { texte: string; ton: string } {
  switch (c.lineup_type) {
    case 'official':
      return { texte: 'Compo officielle', ton: 'text-emerald-400' }
    case 'bzzoiro':
      return { texte: 'Compo probable', ton: 'text-amber-400' }
    case 'probable_manual':
      return { texte: 'Compo saisie à la main', ton: 'text-blue-400' }
    case 'last_known':
      return { texte: 'Dernière compo connue de l’équipe', ton: 'text-gray-400' }
    default:
      return { texte: c.lineup_type, ton: 'text-gray-400' }
  }
}

function BlocCompo({ compo, titre }: { compo: Compo | null; titre: string }) {
  if (!compo) {
    return (
      <div className="bg-gray-800 rounded-xl p-4">
        <h3 className="text-sm font-semibold text-white mb-1">{titre}</h3>
        <p className="text-xs text-gray-500">Aucune compo publiée</p>
      </div>
    )
  }
  const { texte, ton } = origineCompo(compo)
  const titulaires = compo.players.filter((j) => j.is_starter)
  const remplacants = compo.players.filter((j) => !j.is_starter)

  return (
    <div className="bg-gray-800 rounded-xl p-4">
      <div className="flex items-baseline justify-between gap-2 mb-2 flex-wrap">
        <h3 className="text-sm font-semibold text-white">{titre}</h3>
        <span className={clsx('text-xs font-medium', ton)}>{texte}</span>
      </div>
      {compo.published_at && (
        <p className="text-[11px] text-gray-500 mb-2">
          publiée le {new Date(compo.published_at).toLocaleString('fr-FR')}
        </p>
      )}
      <ol className="space-y-0.5 text-sm">
        {titulaires.map((j, i) => (
          <li key={i} className="flex gap-2 text-gray-200">
            <span className="text-gray-600 w-6 text-right font-mono text-xs">
              {j.jersey_number ?? ''}
            </span>
            <span>{j.player_name}</span>
            <span className="text-gray-600 text-xs">{j.position}</span>
          </li>
        ))}
      </ol>
      {remplacants.length > 0 && (
        <>
          <p className="text-[11px] uppercase tracking-wide text-gray-500 mt-3 mb-1">
            Remplaçants
          </p>
          <ol className="space-y-0.5 text-xs text-gray-400">
            {remplacants.map((j, i) => (
              <li key={i} className="flex gap-2">
                <span className="text-gray-600 w-6 text-right font-mono">
                  {j.jersey_number ?? ''}
                </span>
                <span>{j.player_name}</span>
              </li>
            ))}
          </ol>
        </>
      )}
    </div>
  )
}

export default function FicheMatchPage() {
  const { id } = useParams<{ id: string }>()
  const [fiche, setFiche] = useState<FicheMatch | null>(null)
  const [erreur, setErreur] = useState<string | null>(null)

  useEffect(() => {
    fetch(`/api/v1/matches/${id}`)
      .then((r) => {
        if (r.status === 404) throw new Error('Match introuvable')
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.json()
      })
      .then(setFiche)
      .catch((e) => setErreur((e as Error).message))
  }, [id])

  if (erreur) {
    return (
      <div className="flex items-center gap-2 p-3 rounded-lg bg-red-500/10 border border-red-500/30 text-red-300 text-sm">
        <AlertCircle className="w-4 h-4 shrink-0" /> {erreur}
      </div>
    )
  }
  if (!fiche) return <p className="text-gray-500">Chargement…</p>

  return (
    <div className="space-y-4 max-w-5xl">
      <Link href="/dashboard/matches" className="inline-flex items-center gap-1 text-sm text-gray-400 hover:text-white">
        <ArrowLeft className="w-4 h-4" /> Tous les matchs
      </Link>

      <div className="bg-gray-800 rounded-xl px-5 py-4">
        <div className="flex items-center justify-center gap-6 flex-wrap">
          <span className="text-lg font-semibold text-white">{fiche.home_team}</span>
          <span className="text-2xl font-mono text-white">
            {fiche.home_score != null && fiche.away_score != null
              ? `${fiche.home_score} - ${fiche.away_score}`
              : 'à venir'}
          </span>
          <span className="text-lg font-semibold text-white">{fiche.away_team}</span>
        </div>
        {(fiche.home_xg != null || fiche.away_xg != null) && (
          <p className="text-center text-xs text-gray-400 mt-1">
            xG {fiche.home_xg?.toFixed(2) ?? '—'} — {fiche.away_xg?.toFixed(2) ?? '—'}
          </p>
        )}
      </div>

      {/* Un bloc absent se signale : zéro tir et pas de données de tirs ne
          veulent pas dire la même chose. */}
      {fiche.blocs_manquants.length > 0 && (
        <p className="text-xs text-amber-400/80">
          Données pas encore récupérées : {fiche.blocs_manquants.join(', ')}
        </p>
      )}

      <div className="grid gap-4 md:grid-cols-2">
        <BlocCompo compo={fiche.home_lineup} titre={fiche.home_team} />
        <BlocCompo compo={fiche.away_lineup} titre={fiche.away_team} />
      </div>

      {fiche.shotmap.length > 0 && (
        <div className="bg-gray-800 rounded-xl p-4">
          <h3 className="text-sm font-semibold text-white mb-3">
            Carte des tirs · {fiche.shotmap.length} frappes
          </h3>
          <ShotMap
            shots={fiche.shotmap}
            homeTeam={fiche.home_team}
            awayTeam={fiche.away_team}
            side="both"
          />
        </div>
      )}
    </div>
  )
}
