import { Suspense } from 'react'
import { BookOpen, Clock } from 'lucide-react'
import LiveSystemStatus from '@/components/LiveSystemStatus'
import { getServerSession } from 'next-auth'
import { authOptions } from '@/lib/auth'
import { redirect } from 'next/navigation'
import fs from 'fs'
import path from 'path'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

const DOC_SECTIONS = [
  { id: '01-how-ev0-works', label: 'Comment Ev0 calcule', file: '01-how-ev0-works.md' },
  { id: '02-using-the-dashboard', label: 'Guide des onglets', file: '02-using-the-dashboard.md' },
  { id: '03-autopilot', label: 'Autopilot (Agent RL)', file: '03-autopilot.md' },
  { id: '04-data-scraping', label: 'Sources de données', file: '04-data-scraping.md' },
  { id: '05-limitations', label: 'Limitations', file: '05-limitations.md' },
]

function readDocContent(file: string): string {
  // Try multiple paths to work both locally and in Docker
  const candidates = [
    path.join(process.cwd(), '..', 'docs', 'user-guide', file),
    path.join(process.cwd(), 'docs', 'user-guide', file),
    path.join('/app', 'docs', 'user-guide', file),
  ]
  for (const p of candidates) {
    try {
      return fs.readFileSync(p, 'utf-8')
    } catch {
      // try next
    }
  }
  return `# ${file}\n\nDocument non disponible.`
}

function getLastModified(file: string): string {
  const candidates = [
    path.join(process.cwd(), '..', 'docs', 'user-guide', file),
    path.join(process.cwd(), 'docs', 'user-guide', file),
  ]
  for (const p of candidates) {
    try {
      const stat = fs.statSync(p)
      return stat.mtime.toLocaleDateString('fr-FR', { day: '2-digit', month: 'long', year: 'numeric' })
    } catch {
      // try next
    }
  }
  return 'inconnue'
}

interface PageProps {
  searchParams: Promise<{ section?: string }>
}

export default async function DocsPage({ searchParams }: PageProps) {
  const session = await getServerSession(authOptions)
  if (!session) redirect('/login')

  const params = await searchParams
  const activeId = params.section ?? DOC_SECTIONS[0].id
  const activeSection = DOC_SECTIONS.find(s => s.id === activeId) ?? DOC_SECTIONS[0]
  const content = readDocContent(activeSection.file)
  const lastModified = getLastModified(activeSection.file)

  return (
    <div className="p-4 md:p-8 space-y-6">
      {/* Header */}
      <div className="flex items-center gap-3">
        <BookOpen className="h-7 w-7 text-blue-400" />
        <div>
          <h1 className="text-2xl font-bold text-white">Documentation</h1>
          <p className="text-sm text-gray-400">Guide complet d&apos;Ev0</p>
        </div>
      </div>

      {/* Live system status strip */}
      <div className="bg-gray-800/60 border border-gray-700 rounded-lg px-4 py-3">
        <p className="text-xs text-gray-500 mb-2 uppercase tracking-wide">Statut système en direct</p>
        <Suspense fallback={<span className="text-xs text-gray-500">Chargement...</span>}>
          <LiveSystemStatus />
        </Suspense>
      </div>

      {/* Two-column layout */}
      <div className="flex flex-col md:flex-row gap-6">
        {/* Sub-nav */}
        <nav className="md:w-56 shrink-0">
          <ul className="space-y-1">
            {DOC_SECTIONS.map((section) => {
              const isActive = section.id === activeId
              return (
                <li key={section.id}>
                  <a
                    href={`/dashboard/docs?section=${section.id}`}
                    className={`block px-3 py-2 rounded-lg text-sm transition-colors ${
                      isActive
                        ? 'bg-blue-600 text-white font-medium'
                        : 'text-gray-400 hover:bg-gray-800 hover:text-white'
                    }`}
                  >
                    {section.label}
                  </a>
                </li>
              )
            })}
          </ul>
        </nav>

        {/* Doc content */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5 mb-4 text-xs text-gray-500">
            <Clock className="h-3 w-3" />
            Dernière mise à jour : {lastModified}
          </div>
          <div className="bg-gray-900/50 rounded-xl border border-gray-800 p-6">
            <div className="prose prose-invert max-w-none prose-headings:text-white prose-p:text-gray-300 prose-a:text-blue-400 prose-strong:text-white prose-code:text-green-400 prose-pre:bg-gray-900 prose-table:text-gray-300 prose-th:text-white prose-td:border-gray-700 prose-th:border-gray-700">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {content}
              </ReactMarkdown>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
