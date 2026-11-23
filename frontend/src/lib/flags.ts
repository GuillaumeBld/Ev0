/**
 * Code pays ISO (minuscule) lu dans le drapeau emoji que renvoient les API CDM.
 * Un drapeau = deux indicateurs régionaux (🇫🇷 = F + R -> "fr").
 * Renvoie null pour tout ce qui n'est pas un drapeau : jamais d'ISO inventé.
 */
export function emojiToIso(emoji: string | null | undefined): string | null {
  if (!emoji) return null
  const chars = Array.from(emoji)
  if (chars.length !== 2) return null
  const a = chars[0].codePointAt(0) ?? 0
  const b = chars[1].codePointAt(0) ?? 0
  if (a < 0x1f1e6 || a > 0x1f1ff || b < 0x1f1e6 || b > 0x1f1ff) return null
  return (
    String.fromCharCode(a - 0x1f1e6 + 97) + String.fromCharCode(b - 0x1f1e6 + 97)
  )
}

/** Badge « sélection » minimal pour <TeamBadge> : drapeau seul, sans écusson. */
export function flagBadge(emoji: string | null | undefined) {
  const iso = emojiToIso(emoji)
  return iso ? { name_fr: null, crest_url: null, api_football_id: null, country_iso: iso } : null
}
