'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { clsx } from 'clsx'
import { Search, Map as MapIcon, LayoutList, SunMoon, RefreshCw } from 'lucide-react'
import styles from '@/components/atlas/atlas.module.css'
import { WorldMap } from '@/components/atlas/WorldMap'
import { Explorer, type ExplorerContext } from '@/components/atlas/Explorer'
import { CompetitionMenu } from '@/components/atlas/CompetitionMenu'
import { PlayerDetail } from '@/components/atlas/PlayerDetail'
import { CompareModal } from '@/components/atlas/CompareModal'
import { fetchGeo, type PlayerSummary } from '@/lib/atlas'

type Mode = 'map' | 'menu'
type Palette = 'ambre' | 'bleu' | 'cuir'

const foldAccents = (s: string): string =>
  s.normalize('NFD').replace(/\p{Diacritic}/gu, '').toLowerCase()

export default function AtlasPage() {
  const { data: geo, isLoading, isError, refetch } = useQuery({ queryKey: ['atlas-geo'], queryFn: fetchGeo })

  const [mode, setMode] = useState<Mode>('map')
  const [theme, setTheme] = useState<'dark' | 'light'>('dark')
  const [palette, setPalette] = useState<Palette>('ambre')
  const [search, setSearch] = useState('')

  const [selectedIso, setSelectedIso] = useState<string | null>(null)
  const [explorerContext, setExplorerContext] = useState<ExplorerContext | null>(null)
  const [openPlayerId, setOpenPlayerId] = useState<number | null>(null)
  const [compareSelection, setCompareSelection] = useState<PlayerSummary[]>([])

  useEffect(() => {
    try {
      const t = localStorage.getItem('ev0-atlas-theme')
      if (t === 'light' || t === 'dark') setTheme(t)
      const p = localStorage.getItem('ev0-atlas-pal')
      if (p === 'ambre' || p === 'bleu' || p === 'cuir') setPalette(p)
    } catch { /* localStorage indisponible (SSR / navigation privée) */ }
  }, [])

  const openCountry = useCallback((iso: string) => {
    if (!geo) return
    const country = geo.countries.find((c) => c.code === iso)
    if (!country || country.leagues.length === 0) return
    setSelectedIso(iso)
    setExplorerContext({ kind: 'league', country, league: country.leagues[0] })
    setMode('map')
  }, [geo])

  const openIntl = useCallback((leagueApiId: number) => {
    if (!geo) return
    const intl = geo.international.find((i) => i.league_api_id === leagueApiId)
    if (!intl) return
    setExplorerContext({ kind: 'intl', intl })
    setMode('map')
  }, [geo])

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key !== 'Escape') return
      if (compareSelection.length) setCompareSelection([])
      else if (openPlayerId != null) setOpenPlayerId(null)
      else if (explorerContext) setExplorerContext(null)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [compareSelection, openPlayerId, explorerContext])

  function toggleTheme() {
    setTheme((t) => {
      const next = t === 'dark' ? 'light' : 'dark'
      try { localStorage.setItem('ev0-atlas-theme', next) } catch { /* ignore */ }
      return next
    })
  }

  function setPaletteAndPersist(p: Palette) {
    setPalette(p)
    try { localStorage.setItem('ev0-atlas-pal', p) } catch { /* ignore */ }
  }

  function handleSearchSubmit(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key !== 'Enter' || !geo) return
    const q = foldAccents(search)
    const hit = geo.countries.find((c) => foldAccents(c.name).includes(q))
    if (hit) openCountry(hit.code)
  }

  const totalLeagues = useMemo(() => geo?.countries.reduce((s, c) => s + c.leagues.length, 0) ?? 0, [geo])

  return (
    <div
      className={styles.app}
      data-theme={theme}
      data-pal={palette === 'ambre' ? undefined : palette}
    >
      <div className={styles.bar}>
        <div className={styles.brand}>
          <div className={styles.mk}>E0</div>
          <div><b>Ev0 Atlas</b><span>Explorateur de joueurs</span></div>
        </div>
        <div className={styles.cmd}>
          <Search />
          <input
            placeholder="Chercher un pays, un championnat…  (Entrée)"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={handleSearchSubmit}
          />
        </div>
        <div className={styles.modetog}>
          <button className={mode === 'map' ? styles.on : undefined} onClick={() => setMode('map')}>
            <MapIcon size={15} /><span>Carte</span>
          </button>
          <button className={mode === 'menu' ? styles.on : undefined} onClick={() => setMode('menu')}>
            <LayoutList size={15} /><span>Menus</span>
          </button>
        </div>
        <div className={styles.palsel} title="Palette">
          <button className={palette === 'ambre' ? styles.on : undefined} style={{ background: '#e0a24a' }} onClick={() => setPaletteAndPersist('ambre')} />
          <button className={palette === 'bleu' ? styles.on : undefined} style={{ background: '#5e97f0' }} onClick={() => setPaletteAndPersist('bleu')} />
          <button className={palette === 'cuir' ? styles.on : undefined} style={{ background: '#c7515f' }} onClick={() => setPaletteAndPersist('cuir')} />
        </div>
        <div className={styles.iconbtn} onClick={toggleTheme} title={theme === 'dark' ? 'Mode clair' : 'Mode sombre'}>
          <SunMoon />
        </div>
      </div>

      {isLoading ? (
        <div style={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center', color: 'var(--t2)' }}>
          <RefreshCw className="animate-spin" />&nbsp;Chargement de la carte…
        </div>
      ) : isError || !geo ? (
        <div style={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center', gap: 12 }}>
          <p style={{ color: 'var(--t2)' }}>Impossible de charger les données Atlas.</p>
          <button className={styles.chip} onClick={() => refetch()}>Réessayer</button>
        </div>
      ) : totalLeagues === 0 ? (
        <div style={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center', color: 'var(--t2)' }}>
          Aucun championnat référencé pour le moment.
        </div>
      ) : mode === 'menu' ? (
        <div className={clsx(styles.menus, styles.on)}>
          <CompetitionMenu geo={geo} onOpenCountry={openCountry} onOpenIntl={openIntl} />
        </div>
      ) : (
        <WorldMap
          countries={geo.countries}
          international={geo.international}
          selectedIso={selectedIso}
          onSelectCountry={openCountry}
          onOpenInternational={() => setMode('menu')}
        />
      )}

      {explorerContext && geo && (
        <Explorer
          context={explorerContext}
          onBack={() => setExplorerContext(null)}
          onSwitchLeague={(league) => {
            if (explorerContext.kind === 'league') {
              setExplorerContext({ kind: 'league', country: explorerContext.country, league })
            }
          }}
          onOpenPlayer={setOpenPlayerId}
          onOpenCompare={setCompareSelection}
        />
      )}

      <PlayerDetail playerApiId={openPlayerId} onClose={() => setOpenPlayerId(null)} />
      <CompareModal players={compareSelection} onClose={() => setCompareSelection([])} />
    </div>
  )
}
