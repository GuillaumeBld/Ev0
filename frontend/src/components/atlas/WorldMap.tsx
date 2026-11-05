'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { clsx } from 'clsx'
import styles from './atlas.module.css'
import { WORLD_PATHS, BBOX } from './worldGeo.data'
import type { GeoCountry, GeoIntl } from '@/lib/atlas'

/** Couleur par continent — palette ambre de la maquette. */
const CONT: Record<string, string> = {
  EUR: '#e7ab4e',
  SAM: '#49ad73',
  NAM: '#dd8445',
  ASI: '#d35f54',
  AFR: '#c98a3e',
  OCE: '#3fa6ad',
}

const CONT_LABELS: [string, string][] = [
  ['Europe', 'EUR'],
  ['Amérique S.', 'SAM'],
  ['Amérique N.', 'NAM'],
  ['Asie', 'ASI'],
  ['Afrique', 'AFR'],
  ['Océanie', 'OCE'],
]

interface Transform {
  scale: number
  tx: number
  ty: number
}

interface Tip {
  x: number
  y: number
  name: string
  leagueCount: number
}

interface WorldMapProps {
  countries: GeoCountry[]
  international: GeoIntl[]
  selectedIso: string | null
  onSelectCountry: (iso: string) => void
  onOpenInternational: () => void
}

export function WorldMap({
  countries,
  international,
  selectedIso,
  onSelectCountry,
  onOpenInternational,
}: WorldMapProps) {
  const svgRef = useRef<SVGSVGElement>(null)
  const [transform, setTransform] = useState<Transform>({ scale: 1, tx: 0, ty: 0 })
  const [tip, setTip] = useState<Tip | null>(null)
  const dragRef = useRef<{ x0: number; y0: number; tx0: number; ty0: number } | null>(null)

  const byIso = useMemo(() => {
    const m = new Map<string, GeoCountry>()
    for (const c of countries) m.set(c.code, c)
    return m
  }, [countries])

  const totalClubs = useMemo(
    () => countries.reduce((sum, c) => sum + c.leagues.reduce((s, l) => s + l.club_count, 0), 0),
    [countries],
  )

  const focus = useCallback((iso: string) => {
    const b = BBOX[iso]
    if (!b) return
    const [mx, my, mw, mh] = b
    const pad = 2.6
    const s = Math.min(1000 / (mw * pad), 394 / (mh * pad), 9)
    const cx = mx + mw / 2
    const cy = my + mh / 2
    setTransform({ scale: s, tx: 500 - cx * s, ty: 197 - cy * s })
  }, [])

  // Zoom to the newly selected country, reset when deselected (back to map).
  useEffect(() => {
    if (selectedIso) focus(selectedIso)
    else setTransform({ scale: 1, tx: 0, ty: 0 })
  }, [selectedIso, focus])

  useEffect(() => {
    function onMove(e: MouseEvent) {
      const d = dragRef.current
      const svg = svgRef.current
      if (!d || !svg) return
      const k = 1000 / svg.clientWidth
      setTransform((t) => ({
        ...t,
        tx: d.tx0 + (e.clientX - d.x0) * k,
        ty: d.ty0 + (e.clientY - d.y0) * k,
      }))
    }
    function onUp() {
      dragRef.current = null
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
    return () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
  }, [])

  const handleMouseDown = (e: React.MouseEvent) => {
    dragRef.current = { x0: e.clientX, y0: e.clientY, tx0: transform.tx, ty0: transform.ty }
  }

  const zoomIn = () => setTransform((t) => ({ ...t, scale: Math.min(t.scale * 1.4, 9) }))
  const zoomOut = () => setTransform((t) => ({ ...t, scale: Math.max(t.scale / 1.4, 1) }))
  const zoomReset = () => setTransform({ scale: 1, tx: 0, ty: 0 })

  return (
    <div className={styles.stage}>
      <svg
        ref={svgRef}
        className={styles.map}
        id="map"
        viewBox="0 0 1000 394"
        preserveAspectRatio="xMidYMid meet"
        onMouseDown={handleMouseDown}
        onMouseLeave={() => setTip(null)}
      >
        <g
          className={styles.geo}
          transform={`translate(${transform.tx} ${transform.ty}) scale(${transform.scale})`}
        >
          {WORLD_PATHS.map((p, i) => {
            const country = byIso.get(p.code)
            const covered = !!country
            const fill = covered ? CONT[country!.continent] ?? CONT.EUR : undefined
            return (
              <path
                key={`${p.code}-${i}`}
                d={p.d}
                className={clsx(covered && styles.cov, country && selectedIso === country.code && styles.sel)}
                style={covered ? { fill, stroke: 'rgba(255,255,255,.4)' } : undefined}
                onClick={() => country && onSelectCountry(country.code)}
                onMouseMove={(e) => {
                  if (!country) {
                    setTip(null)
                    return
                  }
                  setTip({
                    x: Math.min(e.clientX + 14, window.innerWidth - 200),
                    y: e.clientY + 14,
                    name: country.name,
                    leagueCount: country.leagues.length,
                  })
                }}
              />
            )
          })}
        </g>
      </svg>

      <div className={styles.hero}>
        <div className={styles.eyebrow}>
          {countries.reduce((s, c) => s + c.leagues.length, 0)} championnats · {totalClubs} clubs · {international.length} compétitions internationales
        </div>
        <h1>Le foot mondial,<br />d&apos;un simple clic.</h1>
        <p>Clique un pays pour ouvrir son explorateur de joueurs. Ou passe par les compétitions internationales.</p>
        <span className={styles.chip} onClick={onOpenInternational}>
          <span className={styles.dt} />Compétitions internationales
        </span>
        <div className={styles.legend}>
          {CONT_LABELS.map(([label, key]) => (
            <span className={styles.lg} key={key}>
              <i style={{ background: CONT[key] }} />{label}
            </span>
          ))}
        </div>
      </div>

      <div className={styles.zoomer}>
        <button onClick={zoomIn} aria-label="Zoomer">+</button>
        <button onClick={zoomOut} aria-label="Dézoomer">−</button>
        <button onClick={zoomReset} title="Vue monde" aria-label="Vue monde">⤢</button>
      </div>

      {tip && (
        <div className={clsx(styles.tip, styles.on)} style={{ left: tip.x, top: tip.y }}>
          <b>{tip.name}</b>
          <small>{tip.leagueCount} championnat{tip.leagueCount > 1 ? 's' : ''} · clic pour ouvrir</small>
        </div>
      )}
    </div>
  )
}
