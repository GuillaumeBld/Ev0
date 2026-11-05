'use client'

import styles from './atlas.module.css'
import type { GeoResponse } from '@/lib/atlas'

const CONT: Record<string, string> = {
  EUR: '#e7ab4e', SAM: '#49ad73', NAM: '#dd8445', ASI: '#d35f54', AFR: '#c98a3e', OCE: '#3fa6ad',
}

interface CompetitionMenuProps {
  geo: GeoResponse
  onOpenCountry: (iso: string) => void
  onOpenIntl: (leagueApiId: number) => void
}

export function CompetitionMenu({ geo, onOpenCountry, onOpenIntl }: CompetitionMenuProps) {
  return (
    <div className={styles.wrap}>
      <h2>Compétitions internationales</h2>
      <div className={styles.mrow}>
        {geo.international.map((it) => (
          <div className={styles.mcard} key={it.league_api_id} onClick={() => onOpenIntl(it.league_api_id)}>
            <div style={{
              width: 34, height: 34, borderRadius: 9,
              background: it.org === 'FIFA' ? 'rgba(111,147,184,.18)' : 'var(--a-soft)',
              display: 'grid', placeItems: 'center',
            }}>🏆</div>
            <div>
              <div className={styles.t}>{it.name}</div>
              <div className={styles.s}>{it.kind === 'selection' ? 'Sélections nationales' : 'Compétition de clubs'}</div>
            </div>
            <span className={styles.org}>{it.org}</span>
          </div>
        ))}
      </div>

      <h2>Par pays</h2>
      <div className={styles.mrow}>
        {geo.countries.map((c) => (
          <div className={styles.mcard} key={c.code} onClick={() => onOpenCountry(c.code)}>
            <div style={{
              width: 12, height: 12, borderRadius: '50%', flex: 'none',
              background: CONT[c.continent] ?? CONT.EUR,
            }} />
            <div>
              <div className={styles.t}>{c.name}</div>
              <div className={styles.s}>{c.leagues.map((l) => l.name).join(' · ')}</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
