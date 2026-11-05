'use client'

import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { clsx } from 'clsx'
import { ArrowLeft, Search, LayoutGrid, Columns3, ChevronLeft, ChevronRight, ChevronUp, ChevronDown } from 'lucide-react'
import styles from './atlas.module.css'
import { ClubCrest } from './ClubCrest'
import { PlayerCard, PlayerTableRow } from './PlayerCard'
import {
  fetchPlayers, fetchCanonicalTeams,
  type GeoCountry, type GeoLeague, type GeoIntl,
  type CanonicalTeam, type PlayerSummary, type PlayerSortField,
} from '@/lib/atlas'

export type ExplorerContext =
  | { kind: 'league'; country: GeoCountry; league: GeoLeague }
  | { kind: 'intl'; intl: GeoIntl }

const CONT: Record<string, string> = {
  EUR: '#e7ab4e', SAM: '#49ad73', NAM: '#dd8445', ASI: '#d35f54', AFR: '#c98a3e', OCE: '#3fa6ad',
}

const PAGE_SIZE = 24
const SQUAD_LIMIT = 100 // borne raisonnable pour UN effectif de club, pas un contournement de pagination

const SORT_OPTIONS: { value: PlayerSortField; label: string }[] = [
  { value: 'avg_rating', label: 'Note' },
  { value: 'goals', label: 'Buts' },
  { value: 'goal_assist', label: 'Passes déc.' },
  { value: 'minutes_played', label: 'Minutes' },
  { value: 'name', label: 'Nom' },
]

/** Colonnes du tableau triable de la vue liste, dans l'ordre d'affichage.
 *  `key` est la clé de tri serveur (`sort_by`, `_SORT_FIELD_MAP` côté backend).
 *  `defaultOrder` est le sens appliqué au premier clic sur une colonne inactive —
 *  croissant pour le nom (alphabétique), décroissant pour les stats (meilleur en premier). */
const COLUMNS: { key: PlayerSortField; label: string; defaultOrder: 'asc' | 'desc' }[] = [
  { key: 'name', label: 'Joueur', defaultOrder: 'asc' },
  { key: 'goals', label: 'Buts', defaultOrder: 'desc' },
  { key: 'goal_assist', label: 'PD', defaultOrder: 'desc' },
  { key: 'xg_per_90', label: 'xG/90', defaultOrder: 'desc' },
  { key: 'xa_per_90', label: 'xA/90', defaultOrder: 'desc' },
  { key: 'minutes_played', label: 'Min', defaultOrder: 'desc' },
  { key: 'matches_played', label: 'Matchs', defaultOrder: 'desc' },
  { key: 'avg_rating', label: 'Note', defaultOrder: 'desc' },
]

const POSITIONS: { value: '' | 'G' | 'D' | 'M' | 'F'; label: string }[] = [
  { value: '', label: 'Tous' },
  { value: 'G', label: 'G' },
  { value: 'D', label: 'Déf' },
  { value: 'M', label: 'Mil' },
  { value: 'F', label: 'Att' },
]

interface ExplorerProps {
  context: ExplorerContext
  onBack: () => void
  onSwitchLeague: (league: GeoLeague) => void
  onOpenPlayer: (playerApiId: number) => void
  onOpenCompare: (players: PlayerSummary[]) => void
}

export function Explorer({ context, onBack, onSwitchLeague, onOpenPlayer, onOpenCompare }: ExplorerProps) {
  const leagueApiId = context.kind === 'league' ? context.league.league_api_id : context.intl.league_api_id

  const [position, setPosition] = useState<'' | 'G' | 'D' | 'M' | 'F'>('')
  const [query, setQuery] = useState('')
  const [debouncedQuery, setDebouncedQuery] = useState('')
  const [sortBy, setSortBy] = useState<PlayerSortField>('avg_rating')
  const [sortOrder, setSortOrder] = useState<'asc' | 'desc'>('desc')
  const [view, setView] = useState<'cards' | 'list'>('cards')
  const [compareMode, setCompareMode] = useState(false)
  const [selected, setSelected] = useState<PlayerSummary[]>([])
  const [selectedClub, setSelectedClub] = useState<CanonicalTeam | null>(null)
  const [offset, setOffset] = useState(0)

  // Reset the whole explorer-local state when the division (league) changes,
  // same as switching championships in the reference mockup.
  useEffect(() => {
    setPosition(''); setQuery(''); setDebouncedQuery(''); setSortBy('avg_rating'); setSortOrder('desc')
    setView('cards'); setCompareMode(false); setSelected([]); setSelectedClub(null); setOffset(0)
  }, [leagueApiId])

  useEffect(() => {
    const t = setTimeout(() => setDebouncedQuery(query), 300)
    return () => clearTimeout(t)
  }, [query])

  useEffect(() => { setOffset(0) }, [position, debouncedQuery, sortBy, sortOrder, selectedClub])

  const accent = context.kind === 'league' ? (CONT[context.country.continent] ?? 'var(--a)') : 'var(--a)'

  const clubsQuery = useQuery({ queryKey: ['atlas-canonical-teams'], queryFn: fetchCanonicalTeams, staleTime: 10 * 60 * 1000 })
  const clubs = useMemo(
    () => (clubsQuery.data ?? []).filter((t) => t.league_api_id === leagueApiId).sort((a, b) => a.name_fr.localeCompare(b.name_fr)),
    [clubsQuery.data, leagueApiId],
  )

  // Displayed list — real server pagination for the whole division, a single
  // bounded request for "one club's squad" (SQUAD_LIMIT, not a pagination dodge).
  const playersQuery = useQuery({
    queryKey: ['atlas-players', leagueApiId, selectedClub?.bzz_team_id, position, debouncedQuery, sortBy, sortOrder, selectedClub ? 0 : offset],
    queryFn: () => selectedClub
      ? fetchPlayers({
        team_api_id: selectedClub.bzz_team_id ?? undefined, position: position || undefined,
        q: debouncedQuery || undefined, sort_by: sortBy, sort_order: sortOrder, limit: SQUAD_LIMIT, offset: 0,
      })
      : fetchPlayers({
        league_api_id: leagueApiId, position: position || undefined, q: debouncedQuery || undefined,
        sort_by: sortBy, sort_order: sortOrder, limit: PAGE_SIZE + 1, offset,
      }),
    enabled: leagueApiId != null && (!selectedClub || !!selectedClub.bzz_team_id),
  })

  const hasNext = !selectedClub && (playersQuery.data?.length ?? 0) > PAGE_SIZE
  const displayed = selectedClub ? (playersQuery.data ?? []) : (playersQuery.data ?? []).slice(0, PAGE_SIZE)

  // Effectif complet du club sélectionné (sans filtres), pour la bannière —
  // requête séparée de la liste affichée, qui elle peut être filtrée par poste/recherche.
  const squadStatsQuery = useQuery({
    queryKey: ['atlas-squad-stats', selectedClub?.bzz_team_id],
    queryFn: () => fetchPlayers({ team_api_id: selectedClub!.bzz_team_id ?? undefined, limit: SQUAD_LIMIT, offset: 0, sort_by: 'goals', sort_order: 'desc' }),
    enabled: !!selectedClub?.bzz_team_id,
  })

  function toggleSelect(p: PlayerSummary) {
    setSelected((cur) => {
      const idx = cur.findIndex((x) => x.player_api_id === p.player_api_id)
      if (idx >= 0) return cur.filter((x) => x.player_api_id !== p.player_api_id)
      if (cur.length >= 3) return cur
      return [...cur, p]
    })
  }

  // Tri serveur (sortBy/sortOrder) déclenché par un clic sur une colonne du
  // tableau — le useEffect sur [position, debouncedQuery, sortBy, sortOrder, selectedClub]
  // ci-dessus relance déjà le fetch, donc pas de tri client ici.
  function handleSort(field: PlayerSortField) {
    if (sortBy === field) {
      setSortOrder((o) => (o === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortBy(field)
      setSortOrder(COLUMNS.find((c) => c.key === field)?.defaultOrder ?? 'desc')
    }
  }

  const title = context.kind === 'league' ? context.country.name : context.intl.name
  const subtitleLeague = context.kind === 'league' ? context.league : null

  return (
    <div className={clsx(styles.explorer, styles.on)} style={{ '--exa': accent } as React.CSSProperties}>
      <div className={styles.exbar} />
      <div className={styles.extop}>
        <button className={styles.back} onClick={onBack}><ArrowLeft size={16} />Carte</button>
        <div className={styles.ttl}>
          <div>
            <h1>{title}</h1>
            {context.kind === 'league' ? (
              <div className={styles.sub}>
                {context.league.name} · 2026-27{' '}
                <span className={clsx(styles['cov-b'], styles[context.league.coverage])}>
                  <span className={styles.d} />{context.league.coverage === 'full' ? 'complet' : 'partiel'}
                </span>
              </div>
            ) : (
              <div className={styles.sub}>{context.intl.org} · {context.intl.kind === 'selection' ? 'sélections' : 'clubs'}</div>
            )}
          </div>
        </div>
        {context.kind === 'league' && context.country.leagues.length > 1 && (
          <div className={styles.leaguetabs}>
            {context.country.leagues.map((l) => (
              <button key={l.league_api_id} className={l.league_api_id === subtitleLeague?.league_api_id ? styles.on : undefined} onClick={() => onSwitchLeague(l)}>
                {l.name}
              </button>
            ))}
          </div>
        )}
        <div className={styles.posfilter}>
          {POSITIONS.map((p) => (
            <button key={p.value || 'all'} className={position === p.value ? styles.on : undefined} onClick={() => setPosition(p.value)}>{p.label}</button>
          ))}
        </div>
        <div className={styles.extools}>
          <div className={styles.exsearch}>
            <Search size={15} />
            <input placeholder="Joueur…" value={query} onChange={(e) => setQuery(e.target.value)} />
          </div>
          <select className={styles.exsort} value={sortBy} onChange={(e) => setSortBy(e.target.value as PlayerSortField)}>
            {SORT_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
          <button
            className={styles.vtog} title="Cartes / Liste"
            onClick={() => setView((v) => (v === 'cards' ? 'list' : 'cards'))}
          >
            {view === 'cards' ? <Columns3 size={17} /> : <LayoutGrid size={17} />}
          </button>
          <button
            className={clsx(styles.cmpmode, compareMode && styles.on)}
            onClick={() => { setCompareMode((v) => !v); if (compareMode) setSelected([]) }}
          >
            Comparer
          </button>
          <span className={styles.meta}>
            {playersQuery.isLoading ? 'Chargement…' : `${displayed.length} joueur${displayed.length > 1 ? 's' : ''}`}
          </span>
        </div>
      </div>

      <div className={styles.exbody} style={clubs.length === 0 ? { gridTemplateColumns: '1fr' } : undefined}>
        {clubs.length > 0 && (
          <aside className={styles.clubs}>
            <div className={styles.ch}>Clubs · {clubs.length}</div>
            <div className={clsx(styles.clubitem, !selectedClub && styles.on)} onClick={() => setSelectedClub(null)}>
              <div className={styles.who}><div className={styles.cn}>Tous les clubs</div></div>
            </div>
            {clubs.map((c) => (
              <div key={c.id} className={clsx(styles.clubitem, selectedClub?.id === c.id && styles.on)} onClick={() => setSelectedClub(c)}>
                <ClubCrest crestUrl={c.crest_url} apiFootballId={c.api_football_id} name={c.name_fr} size={30} />
                <div className={styles.who}><div className={styles.cn}>{c.name_fr}</div></div>
              </div>
            ))}
          </aside>
        )}
        <main className={styles.exmain}>
          {selectedClub && (
            <div className={styles.clubbanner} style={{ display: 'flex' }}>
              <ClubCrest crestUrl={selectedClub.crest_url} apiFootballId={selectedClub.api_football_id} name={selectedClub.name_fr} size={48} />
              <div className={styles.bi}>
                <h2>{selectedClub.name_fr}</h2>
                <div className={styles.bs}>
                  {squadStatsQuery.isLoading ? 'Chargement de l’effectif…' : (() => {
                    const squad = squadStatsQuery.data ?? []
                    if (squad.length === 0) return 'Effectif non disponible'
                    const rated = squad.filter((p) => p.avg_rating != null)
                    const avg = rated.length ? (rated.reduce((s, p) => s + (p.avg_rating ?? 0), 0) / rated.length).toFixed(2) : '—'
                    const top = squad[0] // déjà trié par buts desc
                    return `${squad.length} joueurs · note moyenne ${avg}${top.goals ? ` · meilleur buteur ${top.name} (${top.goals} buts)` : ''}`
                  })()}
                </div>
              </div>
              <button className={styles.ball} onClick={() => setSelectedClub(null)}>↩ Tout le championnat</button>
            </div>
          )}

          {playersQuery.isError ? (
            <div className={styles.empty}>Erreur de chargement des joueurs.</div>
          ) : !playersQuery.isLoading && displayed.length === 0 ? (
            <div className={styles.empty}>Aucun joueur pour ces filtres.</div>
          ) : view === 'list' ? (
            <div className={styles.ptablewrap}>
              <table className={styles.ptable}>
                <thead>
                  <tr>
                    <th className={styles.ptdcheck} />
                    {COLUMNS.map((c) => {
                      const active = sortBy === c.key
                      return (
                        <th
                          key={c.key}
                          className={clsx(styles.sortable, active && styles.on, c.key !== 'name' && styles.num)}
                          onClick={() => handleSort(c.key)}
                          aria-sort={active ? (sortOrder === 'asc' ? 'ascending' : 'descending') : undefined}
                        >
                          <span className={styles.thlabel}>
                            {c.label}
                            {active ? (sortOrder === 'asc' ? <ChevronUp size={13} /> : <ChevronDown size={13} />) : null}
                          </span>
                        </th>
                      )
                    })}
                  </tr>
                </thead>
                <tbody>
                  {displayed.map((p) => {
                    const isSel = selected.some((x) => x.player_api_id === p.player_api_id)
                    const onClick = () => (compareMode ? toggleSelect(p) : onOpenPlayer(p.player_api_id))
                    return <PlayerTableRow key={p.player_api_id} player={p} selected={isSel} compareMode={compareMode} onClick={onClick} />
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <div className={styles.players}>
              {displayed.map((p) => {
                const isSel = selected.some((x) => x.player_api_id === p.player_api_id)
                const onClick = () => (compareMode ? toggleSelect(p) : onOpenPlayer(p.player_api_id))
                return <PlayerCard key={p.player_api_id} player={p} selected={isSel} compareMode={compareMode} onClick={onClick} />
              })}
            </div>
          )}

          {!selectedClub && (
            <div style={{ display: 'flex', justifyContent: 'center', gap: 10, padding: '4px 20px 24px' }}>
              <button
                className={styles.back} disabled={offset === 0}
                style={offset === 0 ? { opacity: 0.4, cursor: 'default' } : undefined}
                onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
              >
                <ChevronLeft size={16} />Précédent
              </button>
              <span style={{ color: 'var(--t2)', fontSize: 12.5, alignSelf: 'center' }}>Page {Math.floor(offset / PAGE_SIZE) + 1}</span>
              <button
                className={styles.back} disabled={!hasNext}
                style={!hasNext ? { opacity: 0.4, cursor: 'default' } : undefined}
                onClick={() => setOffset((o) => o + PAGE_SIZE)}
              >
                Suivant<ChevronRight size={16} />
              </button>
            </div>
          )}
        </main>
      </div>

      <div className={clsx(styles.cmpbar, selected.length > 0 && compareMode && styles.on)}>
        <div className={styles.sel}>
          {selected.map((p) => (
            <span className={styles.ct} key={p.player_api_id}>
              <b>{p.name}</b>
              <span className={styles.rm} onClick={() => toggleSelect(p)}>✕</span>
            </span>
          ))}
        </div>
        <span className={styles.hint}>{selected.length}/3</span>
        <button className={styles.go} disabled={selected.length < 2} onClick={() => onOpenCompare(selected)}>
          Comparer →
        </button>
      </div>
    </div>
  )
}
