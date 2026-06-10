// Renders a flag image from flagcdn.com using the ISO code embedded in the emoji.
// Unicode flag emojis are Regional Indicator pairs: 🇫🇷 = RI(F) + RI(R) = "fr".
// Falls back to the raw emoji string if extraction fails (e.g. non-flag emoji).

interface FlagImgProps {
  emoji: string | null | undefined
  size?: number
  className?: string
}

function emojiToIso(emoji: string): string | null {
  const chars = [...emoji]
  if (chars.length !== 2) return null
  const a = chars[0].codePointAt(0) ?? 0
  const b = chars[1].codePointAt(0) ?? 0
  if (a < 0x1f1e6 || a > 0x1f1ff || b < 0x1f1e6 || b > 0x1f1ff) return null
  return (
    String.fromCharCode(a - 0x1f1e6 + 65) +
    String.fromCharCode(b - 0x1f1e6 + 65)
  ).toLowerCase()
}

export function FlagImg({ emoji, size = 20, className }: FlagImgProps) {
  if (!emoji) return null
  const iso = emojiToIso(emoji)
  if (!iso) return <span className={className}>{emoji}</span>
  const w = size <= 20 ? 20 : size <= 40 ? 40 : 80
  return (
    <img
      src={`https://flagcdn.com/w${w}/${iso}.png`}
      width={size}
      alt={iso.toUpperCase()}
      className={className}
      style={{ display: 'inline-block', verticalAlign: 'middle' }}
    />
  )
}
