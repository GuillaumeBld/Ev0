import type { Config } from 'tailwindcss'

const config: Config = {
  content: [
    './src/pages/**/*.{js,ts,jsx,tsx,mdx}',
    './src/components/**/*.{js,ts,jsx,tsx,mdx}',
    './src/app/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      spacing: {
        '13': '3.25rem',
      },
      fontFamily: {
        sans: ['var(--font-dm-sans)', 'system-ui', 'sans-serif'],
        mono: ['var(--font-ibm-plex-mono)', 'ui-monospace', 'monospace'],
      },
      colors: {
        'ev-bg':       'var(--ev-bg)',
        'ev-surface':  'var(--ev-surface)',
        'ev-surface2': 'var(--ev-surface2)',
        'ev-sidebar':  'var(--ev-sidebar)',
        'ev-t1': 'var(--ev-t1)',
        'ev-t2': 'var(--ev-t2)',
        'ev-t3': 'var(--ev-t3)',
        'ev-t4': 'var(--ev-t4)',
        'ev-t5': 'var(--ev-t5)',
        'ev-bd':  'var(--ev-bd)',
        'ev-bd2': 'var(--ev-bd2)',
        'ev-pos':  'var(--ev-pos)',
        'ev-neg':  'var(--ev-neg)',
        'ev-warn': 'var(--ev-warn)',
        brand: {
          50: '#f0fdf4',
          100: '#dcfce7',
          200: '#bbf7d0',
          300: '#86efac',
          400: '#4ade80',
          500: '#22c55e',
          600: '#16a34a',
          700: '#15803d',
          800: '#166534',
          900: '#14532d',
        },
      },
    },
  },
  plugins: [require('@tailwindcss/typography')],
}
export default config
