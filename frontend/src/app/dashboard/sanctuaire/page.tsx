'use client'

import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Search } from 'lucide-react'
import { clsx } from 'clsx'

interface Phase {
  as_of_utc: string
  odds: { h2h?: Record<string, number>; totals?: Record<string, number> }
  xg_home: number
  xg_away: number
}

interface SanctuaryMatch {
  fixture_id: number
  home_team: string
  away_team: string
  league: string | null
  kickoff_utc: string
  opening: Phase | null
  closing: Phase | null
  max_move_pct: number | null
}

const SEUILS = [
  { label: 'Tous', value: '' },
  { label: '> 5 %', value: '5' },
  { label: '> 10 %', value: '10' },
  { label: '> 15 %', value: '15' },
]

async function fetchMatches(params: URLSearchParams): Promise<SanctuaryMatch[]> {
  const res = await fetch(`/api/v1/sanctuary/matches?${params}`)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

async function fetchLeagues(): Promise<string[]> {
  const res = await fetch('/api/v1/sanctuary/leagues')
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

function fmt(n: number | undefined | null, d = 2): string {
  return n === undefined || n === null ? '—' : n.toFixed(d)
}

function kickoff(iso: string): string {
  const dt = new Date(iso)
  return dt.toLocaleString('fr-FR', {
    day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit',
  })
}

/**
 * Une case : cloture en jaune au-dessus, ouverture en bleu en dessous.
 *
 * `close === null` ne dit pas pourquoi la case est vide : le match peut ne
 * pas avoir encore de cloture ("en attente", normal), ou avoir sa cloture
 * sans que ses cotes aient survecu à la purge 45 j des snapshots
 * ("non archivé", un trou d'archive — voir backfill_xg_odds.py). D'où le
 * prop `closePhaseExists`, distinct de la valeur elle-même.
 */
function Cell({
  close, open, closePhaseExists, align = 'left',
}: {
  close: number | null
  open: number | null
  closePhaseExists: boolean
  align?: 'left' | 'center' | 'right'
}) {
  return (
    <div className={clsx('flex flex-col gap-0.5',
      align === 'right' && 'items-end',
      align === 'center' && 'items-center')}>
      {close === null ? (
        <span className={clsx('font-mono text-sm', closePhaseExists ? 'text-ev-warn' : 'text-ev-t4')}>
          {closePhaseExists ? 'non archivé' : 'en attente'}
        </span>
      ) : (
        <span className="font-mono tabular-nums text-xl font-semibold text-ev-close">
          {fmt(close)}
        </span>
      )}
      <span className="font-mono tabular-nums text-xs text-ev-open">{fmt(open)}</span>
    </div>
  )
}

export default function SanctuairePage() {
  const [team, setTeam] = useState('')
  const [league, setLeague] = useState('')
  const [withClosing, setWithClosing] = useState(false)
  const [minMove, setMinMove] = useState('')

  const params = new URLSearchParams()
  if (team) params.set('team', team)
  if (league) params.set('league', league)
  if (withClosing) params.set('with_closing', 'true')
  if (minMove) params.set('min_move', minMove)

  const { data: leagues } = useQuery({ queryKey: ['sanctuary-leagues'], queryFn: fetchLeagues })
  const { data, isLoading, error } = useQuery({
    queryKey: ['sanctuary', params.toString()],
    queryFn: () => fetchMatches(params),
  })

  const clotureForcee = minMove !== ''

  return (
    <div className="space-y-6">
      <header className="space-y-1">
        <h1 className="text-2xl font-semibold text-ev-t1">Sanctuaire</h1>
        <p className="text-sm text-ev-t2 max-w-2xl">
          Ce que le marché disait à l&apos;ouverture de la ligne, et juste avant le coup
          d&apos;envoi. <span className="text-ev-open">Bleu&nbsp;: ouverture.</span>{' '}
          <span className="text-ev-close">Jaune&nbsp;: clôture.</span>
        </p>
      </header>

      <div className="flex flex-wrap items-center gap-3 rounded-lg border border-ev-bd bg-ev-surface p-3">
        <div className="relative">
          <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-ev-t4" />
          <input
            value={team}
            onChange={(e) => setTeam(e.target.value)}
            placeholder="Équipe…"
            className="w-52 rounded-md border border-ev-bd bg-ev-surface2 py-2 pl-8 pr-3 text-sm text-ev-t1 placeholder:text-ev-t4 focus:outline-none focus:ring-2 focus:ring-ev-open"
          />
        </div>

        <select
          value={league}
          onChange={(e) => setLeague(e.target.value)}
          className="rounded-md border border-ev-bd bg-ev-surface2 px-3 py-2 text-sm text-ev-t1 focus:outline-none focus:ring-2 focus:ring-ev-open"
        >
          <option value="">Toutes compétitions</option>
          {(leagues ?? []).map((l) => <option key={l} value={l}>{l}</option>)}
        </select>

        <label className={clsx('flex items-center gap-2 text-sm',
          clotureForcee ? 'text-ev-t3' : 'text-ev-t2')}>
          <input
            type="checkbox"
            checked={withClosing || clotureForcee}
            disabled={clotureForcee}
            onChange={(e) => setWithClosing(e.target.checked)}
            className="accent-ev-close"
          />
          Avec clôture
        </label>

        <select
          value={minMove}
          onChange={(e) => setMinMove(e.target.value)}
          className="rounded-md border border-ev-bd bg-ev-surface2 px-3 py-2 text-sm text-ev-t1 focus:outline-none focus:ring-2 focus:ring-ev-open"
        >
          {SEUILS.map((s) => (
            <option key={s.value} value={s.value}>Mouvement&nbsp;: {s.label}</option>
          ))}
        </select>

        {clotureForcee && (
          <span className="text-xs text-ev-t3">
            Un seuil de mouvement n&apos;a de sens qu&apos;avec une clôture&nbsp;: le filtre est imposé.
          </span>
        )}
      </div>

      {isLoading && <p className="text-sm text-ev-t3">Chargement…</p>}
      {error && <p className="text-sm text-ev-neg">Impossible de charger la bibliothèque.</p>}
      {data && data.length === 0 && (
        <p className="text-sm text-ev-t3">Aucun match ne correspond à ces filtres.</p>
      )}

      <div className="space-y-3">
        {(data ?? []).map((m) => {
          const co = m.closing?.odds?.h2h ?? null
          const oo = m.opening?.odds?.h2h ?? null
          const closePhaseExists = m.closing !== null
          return (
            <article key={m.fixture_id}
              className="rounded-lg border border-ev-bd bg-ev-surface p-4">
              <div className="mb-4 flex items-baseline justify-between gap-4">
                <div className="grid flex-1 grid-cols-[1fr_84px_1fr] items-baseline gap-x-3">
                  <span className="font-medium text-ev-t1">{m.home_team}</span>
                  <span className="text-center text-[11px] uppercase tracking-wider text-ev-t4">nul</span>
                  <span className="text-right font-medium text-ev-t1">{m.away_team}</span>
                </div>
                <div className="flex shrink-0 items-baseline gap-3">
                  {m.max_move_pct !== null && (
                    <span className="font-mono text-xs text-ev-t3">{fmt(m.max_move_pct, 1)} %</span>
                  )}
                  <span className="font-mono text-xs text-ev-t4">{kickoff(m.kickoff_utc)}</span>
                </div>
              </div>

              <div className="border-t border-ev-bd2 pt-3">
                <p className="mb-2 text-[10px] uppercase tracking-wider text-ev-t4">Cote</p>
                <div className="grid grid-cols-[1fr_84px_1fr] gap-x-3">
                  <Cell close={co?.home ?? null} open={oo?.home ?? null} closePhaseExists={closePhaseExists} />
                  <Cell close={co?.draw ?? null} open={oo?.draw ?? null} closePhaseExists={closePhaseExists} align="center" />
                  <Cell close={co?.away ?? null} open={oo?.away ?? null} closePhaseExists={closePhaseExists} align="right" />
                </div>
              </div>

              <div className="mt-3 border-t border-ev-bd2 pt-3">
                <p className="mb-2 text-[10px] uppercase tracking-wider text-ev-t4">xG</p>
                <div className="grid grid-cols-[1fr_84px_1fr] gap-x-3">
                  <Cell close={m.closing?.xg_home ?? null} open={m.opening?.xg_home ?? null} closePhaseExists={closePhaseExists} />
                  {/* Le nul est une issue, pas une equipe : jamais de xG. */}
                  <div className="text-center font-mono text-lg text-ev-t4">—</div>
                  <Cell close={m.closing?.xg_away ?? null} open={m.opening?.xg_away ?? null} closePhaseExists={closePhaseExists} align="right" />
                </div>
              </div>
            </article>
          )
        })}
      </div>
    </div>
  )
}
