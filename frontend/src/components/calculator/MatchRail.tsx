'use client'

import { useMemo } from 'react'
import type { FixtureOut } from '@/lib/api'
import { TeamBadge } from '@/components/TeamBadge'
import s from './calculateur.module.css'

/** Jour local AAAA-MM-JJ d'un coup d'envoi. */
export function dayKey(iso: string): string {
  const d = new Date(iso)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

function dayLabel(key: string): string {
  const [y, m, d] = key.split('-').map(Number)
  const wd = new Date(y, m - 1, d).toLocaleDateString('fr-FR', { weekday: 'short' })
  return `${wd.charAt(0).toUpperCase()}${wd.slice(1)} ${d}`
}

function dayLabelLong(key: string): string {
  const [y, m, d] = key.split('-').map(Number)
  const txt = new Date(y, m - 1, d).toLocaleDateString('fr-FR', { weekday: 'long', day: 'numeric', month: 'long' })
  return txt.charAt(0).toUpperCase() + txt.slice(1)
}

export function kickoffTime(iso: string): string {
  return new Date(iso).toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' })
}

const UNKNOWN_COMP = 'Compétition inconnue'

export function compName(f: FixtureOut): string {
  return f.competition_name ?? UNKNOWN_COMP
}

function initials(name: string): string {
  return name
    .split(/[\s-]+/)
    .filter((w) => w.length > 2 || /^[A-Z0-9]+$/.test(w))
    .map((w) => w[0])
    .join('')
    .toUpperCase()
    .slice(0, 2)
}

function fold(t: string): string {
  return t.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase()
}

export function MatchRail({
  fixtures,
  day,
  onDay,
  query,
  onQuery,
  open,
  onToggle,
  selectedId,
  onPick,
}: {
  fixtures: FixtureOut[]
  day: string | null
  onDay: (d: string) => void
  query: string
  onQuery: (q: string) => void
  open: Record<string, boolean>
  onToggle: (comp: string) => void
  selectedId: number | null
  onPick: (f: FixtureOut) => void
}) {
  const days = useMemo(() => Array.from(new Set(fixtures.map((f) => dayKey(f.kickoff_utc)))).sort(), [fixtures])
  const countByDay = useMemo(() => {
    const m = new Map<string, number>()
    for (const f of fixtures) m.set(dayKey(f.kickoff_utc), (m.get(dayKey(f.kickoff_utc)) ?? 0) + 1)
    return m
  }, [fixtures])
  const dayIndex = day ? days.indexOf(day) : -1
  const goTo = (i: number) => {
    if (i < 0 || i >= days.length) return
    onQuery('')
    onDay(days[i])
  }

  const groups = useMemo(() => {
    const q = fold(query.trim())
    // Une recherche porte sur tous les jours ; sinon on ne montre que le jour choisi.
    const list = fixtures.filter((f) => {
      if (q) {
        const hay = fold(
          [f.home_team, f.away_team, f.home_badge?.name_fr ?? '', f.away_badge?.name_fr ?? '', compName(f)].join(' '),
        )
        return hay.includes(q)
      }
      return dayKey(f.kickoff_utc) === day
    })
    const by = new Map<string, FixtureOut[]>()
    for (const f of list) {
      const k = compName(f)
      by.set(k, [...(by.get(k) ?? []), f])
    }
    return Array.from(by.entries())
      .map(([name, matches]) => ({
        name,
        matches: matches.sort((a, b) => a.kickoff_utc.localeCompare(b.kickoff_utc)),
      }))
      .sort((a, b) => (a.name === UNKNOWN_COMP ? 1 : b.name === UNKNOWN_COMP ? -1 : a.name.localeCompare(b.name, 'fr')))
  }, [fixtures, day, query])

  const searching = query.trim() !== ''

  return (
    <div className={`${s.panel} ${s.rail}`}>
      <label htmlFor="calc-q" className={s.srOnly}>
        Rechercher
      </label>
      <input
        id="calc-q"
        className={s.search}
        placeholder="Match ou équipe"
        value={query}
        onChange={(e) => onQuery(e.target.value)}
      />
      {days.length > 0 && (
        <div className={s.dayNav}>
          <button
            type="button"
            className={s.dayArrow}
            aria-label="Jour précédent"
            disabled={dayIndex <= 0}
            onClick={() => goTo(dayIndex - 1)}
          >
            ‹
          </button>
          <span className={s.dayCurrent}>
            {searching ? 'Tous les jours' : day ? dayLabelLong(day) : '—'}
          </span>
          <button
            type="button"
            className={s.dayArrow}
            aria-label="Jour suivant"
            disabled={dayIndex >= days.length - 1}
            onClick={() => goTo(dayIndex + 1)}
          >
            ›
          </button>
        </div>
      )}
      {/* Tous les jours visibles d'un coup, sur plusieurs lignes : plus de défilement horizontal caché. */}
      <div className={s.dayGrid}>
        {days.map((d) => (
          <button
            key={d}
            type="button"
            className={`${s.chip} ${s.dayChip} ${!searching && d === day ? s.chipOn : ''}`}
            onClick={() => {
              onQuery('')
              onDay(d)
            }}
          >
            <span>{dayLabel(d)}</span>
            <span className={s.dayCount}>{countByDay.get(d) ?? 0}</span>
          </button>
        ))}
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
        {groups.map((g) => {
          const isOpen = searching || !!open[g.name]
          return (
            <div key={g.name}>
              <button className={s.grp} onClick={() => onToggle(g.name)} aria-expanded={isOpen}>
                <span
                  className={s.disc}
                  style={{ width: 26, height: 26, fontSize: 10, background: '#1e2a4a', color: '#b8c9ff' }}
                >
                  {initials(g.name)}
                </span>
                <span style={{ flex: 1, textAlign: 'left' }}>{g.name}</span>
                <span className={s.faint} style={{ fontWeight: 500, fontSize: 12 }}>
                  {g.matches.length}
                </span>
              </button>
              {isOpen && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 2, padding: '2px 0 8px' }}>
                  {g.matches.map((m) => (
                    <button
                      key={m.id}
                      className={`${s.mrow} ${m.id === selectedId ? s.mrowOn : ''}`}
                      onClick={() => onPick(m)}
                    >
                      <span className={s.faint} style={{ fontSize: 12 }}>
                        {searching ? dayLabel(dayKey(m.kickoff_utc)) : kickoffTime(m.kickoff_utc)}
                      </span>
                      <span style={{ display: 'flex', flexDirection: 'column', gap: 3, minWidth: 0 }}>
                        <TeamLine name={m.home_badge?.name_fr ?? m.home_team} badge={m.home_badge} />
                        <TeamLine name={m.away_badge?.name_fr ?? m.away_team} badge={m.away_badge} />
                      </span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          )
        })}
        {groups.length === 0 && (
          <div className={s.faint} style={{ fontSize: 13, padding: '10px 12px' }}>
            Aucun match
          </div>
        )}
      </div>
    </div>
  )
}

function TeamLine({ name, badge }: { name: string; badge: FixtureOut['home_badge'] }) {
  return (
    <span style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
      <TeamBadge badge={badge} name={name} size={16} />
      <span style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{name}</span>
    </span>
  )
}
