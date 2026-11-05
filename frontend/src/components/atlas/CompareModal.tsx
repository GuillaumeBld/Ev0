'use client'

import { clsx } from 'clsx'
import { X } from 'lucide-react'
import styles from './atlas.module.css'
import { ClubCrest } from './ClubCrest'
import type { PlayerSummary } from '@/lib/atlas'

/**
 * Comparaison simplifiée : s'appuie sur les champs déjà chargés par la liste
 * (PlayerSummary), sans aller rechercher le détail segmenté de chacun —
 * livraison priorisée sur la boucle carte → explorateur → fiche joueur,
 * voir le rapport de la tâche pour le détail de ce choix.
 */
const ROWS: [string, (p: PlayerSummary) => string | number][] = [
  ['Note moyenne', (p) => (p.avg_rating != null ? p.avg_rating.toFixed(1) : '—')],
  ['__Finition', () => ''],
  ['Buts', (p) => p.goals ?? '—'],
  ['xG / 90', (p) => (p.xg_per_90 != null ? p.xg_per_90.toFixed(2) : '—')],
  ['Tirs cadrés / 90', (p) => (p.shots_on_target_per_90 != null ? p.shots_on_target_per_90.toFixed(2) : '—')],
  ['__Création', () => ''],
  ['Passes décisives', (p) => p.goal_assist ?? '—'],
  ['xA / 90', (p) => (p.xa_per_90 != null ? p.xa_per_90.toFixed(2) : '—')],
  ['__Temps de jeu', () => ''],
  ['Minutes', (p) => p.minutes_played ?? '—'],
  ['Matchs', (p) => p.matches_played ?? '—'],
]

export function CompareModal({ players, onClose }: { players: PlayerSummary[]; onClose: () => void }) {
  const open = players.length >= 2
  return (
    <div className={clsx(styles.cmpmodal, open && styles.on)} onClick={(e) => e.target === e.currentTarget && onClose()}>
      {open && (
        <div className={styles.box}>
          <div className={styles.ch2}>
            <h2>Comparaison</h2>
            <div className={styles.x} onClick={onClose}><X size={16} /></div>
          </div>
          <table className={styles.cmptable}>
            <thead>
              <tr>
                <th />
                {players.map((p) => (
                  <th key={p.player_api_id}>
                    <ClubCrest crestUrl={p.crest_url} apiFootballId={p.api_football_id} name={p.team_name ?? ''} size={28} />
                    <div style={{ fontSize: 12.5, marginTop: 4 }}>{p.name.split(' ').slice(-1)[0]}</div>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {ROWS.map(([label, getter]) => {
                if (label.startsWith('__')) {
                  return (
                    <tr className={styles.grp} key={label}>
                      <td colSpan={players.length + 1}>{label.slice(2)}</td>
                    </tr>
                  )
                }
                const vals = players.map(getter)
                const nums = vals.map((v) => (typeof v === 'number' ? v : parseFloat(String(v))))
                const max = Math.max(...nums.filter((n) => !Number.isNaN(n)))
                return (
                  <tr key={label}>
                    <td>{label}</td>
                    {vals.map((v, i) => (
                      <td key={i} className={players.length > 1 && !Number.isNaN(nums[i]) && nums[i] === max ? styles.best : undefined}>
                        {v}
                      </td>
                    ))}
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
