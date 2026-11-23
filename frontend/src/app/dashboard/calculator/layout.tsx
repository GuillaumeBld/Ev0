import { Inter } from 'next/font/google'

// Police de la maquette D validée (calculateur-maquette-D.html).
const inter = Inter({ subsets: ['latin'], weight: ['400', '500', '600', '700'], variable: '--font-inter' })

export default function CalculatorLayout({ children }: { children: React.ReactNode }) {
  return <div className={inter.variable}>{children}</div>
}
