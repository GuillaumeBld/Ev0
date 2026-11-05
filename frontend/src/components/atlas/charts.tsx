'use client'

import { useId } from 'react'
import styles from './atlas.module.css'
import { ratingColor } from './PlayerCard'

/**
 * Inline SVG chart primitives for the player "grimoire" page. No external
 * charting lib (CSP blocks remote scripts/styles) — these are a direct React
 * port of the `miniChart`, `barsGX` and `radar` helpers in
 * `docs/superpowers/atlas-reference-mockup.html`, built as real SVG elements
 * instead of template-string markup, and fed with real API values instead of
 * the mockup's random placeholders.
 */

// ── Évolution de la note (courbe + aire) ────────────────────────────────────

export function MiniChart({ values }: { values: number[] }) {
  const gradientId = useId()
  const W = 640
  const H = 170
  const PAD = 18
  const n = values.length
  if (n < 2) {
    return <div className={styles.pempty}>Historique de notes insuffisant.</div>
  }
  const mn = Math.min(...values) - 0.4
  const mx = Math.max(...values) + 0.4
  const x = (i: number) => PAD + (i * (W - 2 * PAD)) / (n - 1)
  const y = (v: number) => H - PAD - ((v - mn) / (mx - mn)) * (H - 2 * PAD)
  const pts = values.map((v, i) => [x(i), y(v)] as const)
  const line = pts.map(([px, py], i) => `${i ? 'L' : 'M'}${px.toFixed(1)} ${py.toFixed(1)}`).join(' ')
  const area = `${line} L${x(n - 1).toFixed(1)} ${H - PAD} L${PAD} ${H - PAD} Z`
  return (
    <svg className={styles.chart} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none">
      <defs>
        <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="var(--a)" stopOpacity={0.3} />
          <stop offset="1" stopColor="var(--a)" stopOpacity={0} />
        </linearGradient>
      </defs>
      {[0, 0.5, 1].map((f) => {
        const yy = (PAD + f * (H - 2 * PAD)).toFixed(1)
        return <line key={f} x1={PAD} x2={W - PAD} y1={yy} y2={yy} stroke="var(--bd)" />
      })}
      <path d={area} fill={`url(#${gradientId})`} />
      <path d={line} fill="none" stroke="var(--a)" strokeWidth={2.5} strokeLinecap="round" strokeLinejoin="round" />
      {pts.map(([px, py], i) => (
        <circle
          key={i}
          cx={px.toFixed(1)}
          cy={py.toFixed(1)}
          r={i === n - 1 ? 4.5 : 2.5}
          fill={i === n - 1 ? 'var(--a)' : ratingColor(values[i])}
        />
      ))}
    </svg>
  )
}

// ── Buts vs xG (barres groupées) ────────────────────────────────────────────

export interface GxBar {
  label: string
  goals: number
  /** null = pas de xG connu sur la période (aucun match avec xG renseigné). */
  xg: number | null
}

export function BarsGX({ data }: { data: GxBar[] }) {
  const W = 640
  const H = 170
  const PAD = 22
  const n = data.length
  if (n === 0) {
    return <div className={styles.pempty}>Pas assez de matchs pour ce graphique.</div>
  }
  const bw = (W - 2 * PAD) / n
  const mx = Math.max(2, ...data.flatMap((d) => [d.goals, d.xg ?? 0]))
  const y = (v: number) => H - PAD - (v / mx) * (H - 2 * PAD)
  return (
    <svg className={styles.chart} viewBox={`0 0 ${W} ${H}`}>
      {data.map((d, i) => {
        const x0 = PAD + i * bw
        const gy = y(d.goals)
        return (
          <g key={i}>
            <rect
              x={(x0 + bw * 0.16).toFixed(1)}
              y={gy.toFixed(1)}
              width={(bw * 0.3).toFixed(1)}
              height={Math.max(0, H - PAD - gy).toFixed(1)}
              fill="var(--a)"
              rx={2}
            />
            {d.xg != null && (() => {
              const xgy = y(d.xg as number)
              return (
                <rect
                  x={(x0 + bw * 0.52).toFixed(1)}
                  y={xgy.toFixed(1)}
                  width={(bw * 0.3).toFixed(1)}
                  height={Math.max(0, H - PAD - xgy).toFixed(1)}
                  fill="var(--a2)"
                  opacity={0.75}
                  rx={2}
                />
              )
            })()}
            <text x={(x0 + bw / 2).toFixed(1)} y={H - 7} fontSize={10} fill="var(--t3)" textAnchor="middle">
              {d.label}
            </text>
          </g>
        )
      })}
    </svg>
  )
}

// ── Radar de profil ──────────────────────────────────────────────────────────

export interface RadarAxis {
  label: string
  /** 0..100 */
  value: number
}

export function Radar({ axes }: { axes: RadarAxis[] }) {
  const cx = 150
  const cy = 130
  const R = 92
  const n = axes.length
  const pt = (i: number, r: number): readonly [number, number] => {
    const a = -Math.PI / 2 + (i * 2 * Math.PI) / n
    return [cx + Math.cos(a) * r, cy + Math.sin(a) * r]
  }
  if (n === 0) return null
  return (
    <svg className={styles.radar} viewBox="0 0 300 260">
      {[0.25, 0.5, 0.75, 1].map((f) => (
        <polygon
          key={f}
          points={axes.map((_, i) => pt(i, R * f).map((v) => v.toFixed(1)).join(',')).join(' ')}
          fill="none"
          stroke="var(--bd)"
        />
      ))}
      {axes.map((_, i) => {
        const [x, y] = pt(i, R)
        return <line key={i} x1={cx} y1={cy} x2={x.toFixed(1)} y2={y.toFixed(1)} stroke="var(--bd)" />
      })}
      <polygon
        points={axes
          .map((a, i) => pt(i, (R * Math.max(0, Math.min(100, a.value))) / 100).map((v) => v.toFixed(1)).join(','))
          .join(' ')}
        fill="var(--a)"
        fillOpacity={0.25}
        stroke="var(--a)"
        strokeWidth={2}
      />
      {axes.map((a, i) => {
        const [x, y] = pt(i, R + 15)
        return (
          <text key={i} x={x.toFixed(1)} y={y.toFixed(1)} fontSize={10} fill="var(--t2)" textAnchor="middle" dominantBaseline="middle">
            {a.label}
          </text>
        )
      })}
    </svg>
  )
}
