import type { Metadata } from 'next'
import { DM_Sans, IBM_Plex_Mono } from 'next/font/google'
import './globals.css'
import { Providers } from './providers'
import { ThemeProvider } from '@/lib/theme'
import { ThemeToggle } from '@/components/ThemeToggle'

const dmSans = DM_Sans({
  subsets: ['latin'],
  variable: '--font-dm-sans',
})

const ibmPlexMono = IBM_Plex_Mono({
  subsets: ['latin'],
  weight: ['400', '500', '600'],
  variable: '--font-ibm-plex-mono',
})

export const metadata: Metadata = {
  title: 'Ev0 - Prematch Value Engine',
  description: 'Player props pricing and recommendations',
}

export const viewport = {
  width: 'device-width',
  initialScale: 1,
  viewportFit: 'cover' as const,
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en">
      {/* Anti-flash: apply saved theme before first paint */}
      <head>
        <script dangerouslySetInnerHTML={{ __html: `try{if(localStorage.getItem('ev0-theme')==='light')document.documentElement.classList.add('light')}catch(e){}` }} />
      </head>
      <body className={`${dmSans.variable} ${ibmPlexMono.variable} font-sans antialiased`}>
        <Providers>
          <ThemeProvider>
            {children}
            <ThemeToggle />
          </ThemeProvider>
        </Providers>
      </body>
    </html>
  )
}
