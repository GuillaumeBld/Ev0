'use client'

import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useRouter, useSearchParams } from 'next/navigation'
import {
  getFixtureLineups,
  getFixturePlayerOdds,
  getFixtures,
  getPenTakers,
  priceMatch,
  setPenTakers,
  type FixtureOut,
  type FixtureTeamLineup,
  type MatchPriceResponse,
} from '@/lib/api'
import { MatchRail, compName, dayKey, kickoffTime } from '@/components/calculator/MatchRail'
import { TeamOddsTable, type TeamMode } from '@/components/calculator/TeamOddsTable'
import { fmtXg, indexBookOdds, type BookOddsByPlayer } from '@/components/calculator/format'
import s from '@/components/calculator/calculateur.module.css'

type Side = 'home' | 'away'
type PerSide<T> = Record<Side, T>

/** Minimum de titulaires reconnus pour que le moteur applique une compo (team_xg.MIN_LINEUP_STARTERS). */
const MIN_STARTERS = 5

const isPlaceholder = (t: string) => /^[WL]\d+|TBD/i.test(t)

/** Âge des cotes du marché, comme l'ancien bandeau de fraîcheur (« il y a 12 min », « il y a 2h05 »). */
function fmtAge(iso: string): string {
  const mins = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 60000))
  if (mins < 60) return `il y a ${mins} min`
  const h = Math.floor(mins / 60)
  const rem = mins % 60
  return rem > 0 ? `il y a ${h}h${String(rem).padStart(2, '0')}` : `il y a ${h}h`
}

/** Pas et plancher de l'ajustement manuel du xG. */
const XG_STEP = 0.1
const XG_MIN = 0.1

/** Signalement API sans le préfixe technique « team=X: ». */
const cleanWarning = (w: string) => w.replace(/^team=[^:]*:\s*/, '')

function lineupNote(
  lineup: FixtureTeamLineup | null | undefined,
  lineupFailed: boolean,
  checked: number,
  warnings: string[],
): string {
  const parts: string[] = []
  if (lineupFailed) parts.push('Compo indisponible (erreur de chargement) : coche les titulaires.')
  else if (!lineup?.starter_ids?.length) parts.push('Pas de compo connue : coche les titulaires.')
  else if (lineup.lineup_type === 'official') parts.push('Compo officielle.')
  else parts.push('Compo probable — à confirmer à l’annonce officielle.')
  parts.push('Gardien non coté.')
  if (checked < MIN_STARTERS) parts.push(`Coche au moins ${MIN_STARTERS} titulaires pour recalculer.`)
  return [...parts, ...warnings.map(cleanWarning)].join(' ')
}

function CalculatorInner() {
  const searchParams = useSearchParams()
  const router = useRouter()
  const matchParam = searchParams.get('match')

  // ── Bande gauche ──
  const [fixtures, setFixtures] = useState<FixtureOut[]>([])
  const [fixturesError, setFixturesError] = useState(false)
  const [day, setDay] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState<Record<string, boolean>>({})
  const [selectedId, setSelectedId] = useState<number | null>(matchParam ? Number(matchParam) || null : null)

  // ── Match choisi ──
  const [ready, setReady] = useState(false)
  const [pens, setPens] = useState<PerSide<number | null>>({ home: null, away: null })
  const [lineups, setLineups] = useState<PerSide<FixtureTeamLineup | null>>({ home: null, away: null })
  const [lineupFailed, setLineupFailed] = useState(false)
  const [mode, setMode] = useState<PerSide<TeamMode>>({ home: 'squad', away: 'squad' })
  const [starters, setStarters] = useState<PerSide<number[]>>({ home: [], away: [] })
  const [bookOdds, setBookOdds] = useState<BookOddsByPlayer>(new Map())
  const [bookError, setBookError] = useState(false)
  const [pricing, setPricing] = useState<MatchPriceResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // xG ajusté à la main (null = xG du marché) et cotes du marché trop anciennes (503 du calcul).
  const [xgOv, setXgOv] = useState<PerSide<number | null>>({ home: null, away: null })
  const [staleSince, setStaleSince] = useState<string | null>(null)
  const reqSeq = useRef(0)
  const selectedIdRef = useRef(selectedId)
  selectedIdRef.current = selectedId

  // Lien direct (?match=) depuis une autre page alors que le calculateur est ouvert.
  useEffect(() => {
    const id = matchParam ? Number(matchParam) || null : null
    if (id != null) setSelectedId(id)
  }, [matchParam])

  // Matchs à venir et en cours (compétitions de clubs : une sélection n'a ni effectif ni stats).
  useEffect(() => {
    Promise.all([
      // 14 jours à partir d'aujourd'hui. Toutes les compétitions de clubs de l'Atlas sont au
      // calendrier : environ 70 matchs par jour, 1 000 sur la fenêtre.
      getFixtures({
        status: 'scheduled',
        limit: 3000,
        from_date: dayKey(new Date().toISOString()),
        to_date: dayKey(new Date(Date.now() + 13 * 86_400_000).toISOString()),
        clubs_only: true,
      }),
      getFixtures({ status: 'live', limit: 300, clubs_only: true }),
    ])
      .then(([sched, live]) => {
        const seen = new Set<number>()
        const merged: FixtureOut[] = []
        for (const f of [...live.fixtures, ...sched.fixtures]) {
          if (seen.has(f.id) || isPlaceholder(f.home_team) || isPlaceholder(f.away_team)) continue
          seen.add(f.id)
          merged.push(f)
        }
        setFixtures(merged)
        const sel = merged.find((f) => f.id === selectedIdRef.current)
        const today = dayKey(new Date().toISOString())
        const days = Array.from(new Set(merged.map((f) => dayKey(f.kickoff_utc)))).sort()
        const d = sel ? dayKey(sel.kickoff_utc) : days.find((k) => k >= today) ?? days[0] ?? null
        setDay(d)
        const first = sel ?? merged
          .filter((f) => dayKey(f.kickoff_utc) === d)
          .sort(
            (a, b) =>
              (a.competition_rank ?? Number.MAX_SAFE_INTEGER) - (b.competition_rank ?? Number.MAX_SAFE_INTEGER) ||
              compName(a).localeCompare(compName(b), 'fr'),
          )[0]
        if (first) setOpen({ [compName(first)]: true })
      })
      .catch(() => setFixturesError(true))
  }, [])
  // Nouveau match : compo, tireurs de penalty enregistrés et cotes books, en parallèle.
  useEffect(() => {
    // Invalide tout calcul encore en vol pour l'ancien match : sa réponse sera ignorée.
    reqSeq.current++
    setLoading(false)
    setReady(false)
    setPricing(null)
    setError(null)
    setBookOdds(new Map())
    setBookError(false)
    setLineups({ home: null, away: null })
    setLineupFailed(false)
    setMode({ home: 'squad', away: 'squad' })
    setStarters({ home: [], away: [] })
    setPens({ home: null, away: null })
    setXgOv({ home: null, away: null })
    setStaleSince(null)
    if (selectedId == null) return
    let cancelled = false
    Promise.allSettled([getFixtureLineups(selectedId), getPenTakers(selectedId), getFixturePlayerOdds(selectedId)]).then(
      ([lu, pt, bo]) => {
        if (cancelled) return
        if (lu.status === 'fulfilled') {
          setLineups({ home: lu.value.home, away: lu.value.away })
          setStarters({ home: lu.value.home?.starter_ids ?? [], away: lu.value.away?.starter_ids ?? [] })
        } else setLineupFailed(true)
        if (pt.status === 'fulfilled') setPens({ home: pt.value.home_pen_taker_id, away: pt.value.away_pen_taker_id })
        if (bo.status === 'fulfilled') setBookOdds(indexBookOdds(bo.value))
        else setBookError(true)
        setReady(true)
      },
    )
    return () => {
      cancelled = true
    }
  }, [selectedId])

  // Calcul : à chaque changement de mode ou de titulaires (légère temporisation entre deux clics).
  // Le seuil compte les mêmes titulaires que le compteur « N / 10 » : ceux présents dans l'effectif.
  // Les identifiants inconnus restent envoyés pour que l'API les signale.
  const inSquad = (side: Side) => {
    const squad = side === 'home' ? pricing?.home_players : pricing?.away_players
    const ids = new Set((squad ?? []).map((p) => p.player_id))
    return starters[side].filter((id) => ids.has(id)).length
  }
  const homeIds = mode.home === 'xi' && inSquad('home') >= MIN_STARTERS ? starters.home : null
  const awayIds = mode.away === 'xi' && inSquad('away') >= MIN_STARTERS ? starters.away : null
  const homeKey = homeIds?.join(',') ?? ''
  const awayKey = awayIds?.join(',') ?? ''
  useEffect(() => {
    if (!ready || selectedId == null) return
    const seq = ++reqSeq.current
    const t = setTimeout(() => {
      setLoading(true)
      priceMatch({
        fixture_id: selectedId,
        home_xg_override: xgOv.home,
        away_xg_override: xgOv.away,
        home_pen_taker_override: pens.home,
        away_pen_taker_override: pens.away,
        home_starter_ids: homeKey ? homeKey.split(',').map(Number) : null,
        away_starter_ids: awayKey ? awayKey.split(',').map(Number) : null,
      })
        .then((res) => {
          if (seq !== reqSeq.current || res.fixture_id !== selectedId) return
          setPricing(res)
          setError(null)
          setStaleSince(null)
        })
        .catch((e: any) => {
          if (seq !== reqSeq.current) return
          const detail = e?.response?.data?.detail
          if (e?.response?.status === 503) {
            // Même règle que l'ancienne page : 503 = pas de cotes du marché assez récentes
            // pour en tirer le xG (MarketXgService.MAX_SNAPSHOT_AGE) ; plus de calcul affiché.
            setPricing(null)
            if (detail?.last_scraped_at) {
              setStaleSince(detail.last_scraped_at)
              setError(null)
            } else {
              setStaleSince(null)
              setError('Pas de cotes du marché pour ce match.')
            }
            return
          }
          setError(
            typeof detail === 'string'
              ? detail
              : detail?.message ?? 'Erreur lors du calcul des cotes',
          )
        })
        .finally(() => {
          if (seq === reqSeq.current) setLoading(false)
        })
    }, 250)
    return () => clearTimeout(t)
  }, [ready, selectedId, pens.home, pens.away, homeKey, awayKey, xgOv.home, xgOv.away])

  // Tireur de penalty : un par équipe ; re-cliquer le tireur choisi revient à la détection automatique.
  const choosePen = (side: Side) => (playerId: number) => {
    if (selectedId == null) return
    const next = { ...pens, [side]: pens[side] === playerId ? null : playerId }
    setPens(next)
    setPenTakers(selectedId, next.home, next.away).catch(() =>
      setError('Tireur de penalty non enregistré (erreur) : appliqué à ce calcul seulement.'),
    )
  }

  const bumpXg = (side: Side, delta: number) => {
    if (!pricing) return
    const base = xgOv[side] ?? (side === 'home' ? pricing.home_match_xg : pricing.away_match_xg)
    const next = Math.max(XG_MIN, Math.round((base + delta) * 100) / 100)
    setXgOv((cur) => ({ ...cur, [side]: next }))
  }
  const adjusted = xgOv.home != null || xgOv.away != null

  const pick = useCallback(
    (f: FixtureOut) => {
      router.replace(`?match=${f.id}`, { scroll: false })
      setSelectedId(f.id)
    },
    [router],
  )

  const toggleStarter = (side: Side) => (playerId: number) =>
    setStarters((cur) => ({
      ...cur,
      [side]: cur[side].includes(playerId) ? cur[side].filter((id) => id !== playerId) : [...cur[side], playerId],
    }))

  const fixture = useMemo(() => fixtures.find((f) => f.id === selectedId) ?? null, [fixtures, selectedId])

  const homeName = pricing ? pricing.home_badge?.name_fr ?? pricing.home_team : fixture?.home_badge?.name_fr ?? fixture?.home_team
  const awayName = pricing ? pricing.away_badge?.name_fr ?? pricing.away_team : fixture?.away_badge?.name_fr ?? fixture?.away_team
  const competition = pricing?.competition_name ?? fixture?.competition_name ?? null
  const kickoff = fixture
    ? `${new Date(fixture.kickoff_utc).toLocaleDateString('fr-FR', { weekday: 'long', day: 'numeric', month: 'long' })} · ${kickoffTime(fixture.kickoff_utc)}`
    : null
  const scrapedAt = pricing?.last_scraped_at ? kickoffTime(pricing.last_scraped_at) : null

  const sides: Side[] = ['home', 'away']

  return (
    <div className={s.ev}>
      <div className={s.wrap}>
        <MatchRail
          fixtures={fixtures}
          day={day}
          onDay={setDay}
          query={query}
          onQuery={setQuery}
          open={open}
          onToggle={(c) => setOpen((o) => ({ ...o, [c]: !o[c] }))}
          selectedId={selectedId}
          onPick={pick}
        />

        <div className={s.main}>
          {fixturesError && (
            <div className={s.faint} style={{ fontSize: 13 }}>
              Liste des matchs indisponible (erreur de chargement).
            </div>
          )}
          {selectedId == null ? (
            <div className={s.faint} style={{ fontSize: 14 }}>
              Choisis un match dans la liste.
            </div>
          ) : (
            <>
              <div>
                <div className={s.faint} style={{ fontSize: 13 }}>
                  {[competition, kickoff].filter(Boolean).join(' · ')}
                </div>
                <div style={{ fontSize: 24, fontWeight: 700, marginTop: 4 }}>
                  {homeName && awayName ? `${homeName} – ${awayName}` : 'Chargement…'}
                </div>
                {pricing && (
                  <div className={s.faint} style={{ fontSize: 14, marginTop: 6 }}>
                    xG du match :{' '}
                    {sides.map((side, i) => (
                      <span key={side}>
                        {i > 0 && ' · '}
                        {side === 'home' ? homeName : awayName}{' '}
                        <button className={s.xgBtn} onClick={() => bumpXg(side, -XG_STEP)} aria-label={`Baisser le xG de ${side === 'home' ? homeName : awayName}`}>
                          −
                        </button>
                        <b style={{ color: '#e6edf3' }}>
                          {fmtXg(xgOv[side] ?? (side === 'home' ? pricing.home_match_xg : pricing.away_match_xg))}
                        </b>
                        <button className={s.xgBtn} onClick={() => bumpXg(side, XG_STEP)} aria-label={`Monter le xG de ${side === 'home' ? homeName : awayName}`}>
                          +
                        </button>
                      </span>
                    ))}
                    {adjusted ? (
                      <>
                        {' — ajusté à la main · '}
                        <button className={s.linkBtn} onClick={() => setXgOv({ home: null, away: null })}>
                          Revenir au marché
                        </button>
                      </>
                    ) : (
                      scrapedAt && ` — cotes du marché à ${scrapedAt}`
                    )}
                  </div>
                )}
                {staleSince && (
                  <div style={{ fontSize: 13, marginTop: 6, color: '#ffc98a' }}>
                    Cotes du marché anciennes ({fmtAge(staleSince)})
                  </div>
                )}
                {error && (
                  <div style={{ fontSize: 13, marginTop: 6, color: '#ffc98a' }}>{error}</div>
                )}
                {bookError && (
                  <div style={{ fontSize: 13, marginTop: 6, color: '#ffc98a' }}>
                    Cotes Betclic, Unibet et PMU indisponibles (erreur de chargement).
                  </div>
                )}
              </div>

              {pricing && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 24, opacity: loading ? 0.6 : 1, transition: 'opacity .15s' }}>
                  {sides.map((side) => {
                    const squad = side === 'home' ? pricing.home_players : pricing.away_players
                    const squadIds = new Set(squad.map((p) => p.player_id))
                    const checked = starters[side].filter((id) => squadIds.has(id)).length
                    return (
                      <TeamOddsTable
                        key={side}
                        name={(side === 'home' ? homeName : awayName) ?? ''}
                        badge={side === 'home' ? pricing.home_badge : pricing.away_badge}
                        xg={side === 'home' ? pricing.home_match_xg : pricing.away_match_xg}
                        mode={mode[side]}
                        onMode={(m) => setMode((cur) => ({ ...cur, [side]: m }))}
                        squad={squad}
                        lineup={side === 'home' ? pricing.home_lineup_players : pricing.away_lineup_players}
                        starterIds={starters[side]}
                        onToggleStarter={toggleStarter(side)}
                        note={lineupNote(
                          lineups[side],
                          lineupFailed,
                          checked,
                          (side === 'home' ? pricing.home_lineup_warnings : pricing.away_lineup_warnings) ?? [],
                        )}
                        bookOdds={bookOdds}
                        penTakerId={pens[side]}
                        onPenTaker={choosePen(side)}
                      />
                    )
                  })}
                </div>
              )}

              <div className={s.faint} style={{ fontSize: 12, lineHeight: 1.6 }}>
                Alpha = calcul actuel (saison), Beta = saison + carrière. Cotes Betclic, Unibet et PMU telles que
                récupérées ; — = cote non proposée. En vert : cote book au-dessus des deux cotes Ev0 de plus de 5 %.
                Mode Compo : l’xG de l’équipe est réparti entre les titulaires cochés.
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

export default function CalculatorPage() {
  return (
    <Suspense>
      <CalculatorInner />
    </Suspense>
  )
}
