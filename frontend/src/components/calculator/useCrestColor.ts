'use client'

import { useEffect, useState } from 'react'
import type { TeamBadgeOut } from '@/lib/api'

export type Rgb = [number, number, number]

/** Valeur neutre (gris-bleu) quand l'écusson ne livre pas de couleur. */
export const NEUTRAL_RGB: Rgb = [122, 134, 153]

/**
 * Couleur dominante de l'écusson Atlas (mêmes sources que ClubCrest :
 * crest_url puis api_football_id). Lecture des pixels via canvas ; si
 * l'image est absente, refusée (CORS) ou sans couleur franche, on rend la
 * valeur neutre — jamais une couleur devinée d'après le nom du club.
 */
export function useCrestColor(badge: TeamBadgeOut | null | undefined): Rgb {
  const [rgb, setRgb] = useState<Rgb>(NEUTRAL_RGB)
  const crestUrl = badge?.crest_url ?? null
  const apiId = badge?.api_football_id ?? null

  useEffect(() => {
    setRgb(NEUTRAL_RGB)
    const sources: string[] = []
    if (crestUrl) sources.push(crestUrl)
    if (apiId) sources.push(`https://media.api-sports.io/football/teams/${apiId}.png`)
    let cancelled = false

    const tryAt = (i: number) => {
      if (cancelled || i >= sources.length) return
      const img = new Image()
      img.crossOrigin = 'anonymous'
      img.onload = () => {
        if (cancelled) return
        const c = dominantColor(img)
        if (c) setRgb(c)
        else tryAt(i + 1)
      }
      img.onerror = () => tryAt(i + 1)
      img.src = sources[i]
    }
    tryAt(0)
    return () => {
      cancelled = true
    }
  }, [crestUrl, apiId])

  return rgb
}

/** Teinte la plus représentée parmi les pixels opaques et saturés. */
function dominantColor(img: HTMLImageElement): Rgb | null {
  try {
    const n = 32
    const canvas = document.createElement('canvas')
    canvas.width = n
    canvas.height = n
    const ctx = canvas.getContext('2d')
    if (!ctx) return null
    ctx.drawImage(img, 0, 0, n, n)
    const data = ctx.getImageData(0, 0, n, n).data
    const buckets = new Map<number, { r: number; g: number; b: number; k: number }>()
    for (let i = 0; i < data.length; i += 4) {
      const [r, g, b, a] = [data[i], data[i + 1], data[i + 2], data[i + 3]]
      if (a < 200) continue
      const max = Math.max(r, g, b)
      const min = Math.min(r, g, b)
      if (max < 40 || max - min < 50) continue // noir, blanc, gris : pas une couleur de club
      const key = ((r >> 5) << 6) | ((g >> 5) << 3) | (b >> 5)
      const cur = buckets.get(key) ?? { r: 0, g: 0, b: 0, k: 0 }
      cur.r += r
      cur.g += g
      cur.b += b
      cur.k += 1
      buckets.set(key, cur)
    }
    let best: { r: number; g: number; b: number; k: number } | null = null
    buckets.forEach((v) => {
      if (!best || v.k > best.k) best = v
    })
    const top = best as { r: number; g: number; b: number; k: number } | null
    if (!top || top.k < 12) return null
    return [Math.round(top.r / top.k), Math.round(top.g / top.k), Math.round(top.b / top.k)]
  } catch {
    return null // canvas « teinté » : pas de lecture possible
  }
}
