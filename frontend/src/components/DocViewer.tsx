import fs from 'fs'
import path from 'path'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

interface DocViewerProps {
  docPath: string  // filename relative to docs/user-guide/ e.g. "01-how-ev0-works.md"
}

export default function DocViewer({ docPath }: DocViewerProps) {
  const fullPath = path.join(process.cwd(), '..', 'docs', 'user-guide', docPath)

  let content = ''
  try {
    content = fs.readFileSync(fullPath, 'utf-8')
  } catch {
    content = '# Document non trouvé\n\nCe fichier n\'existe pas encore.'
  }

  return (
    <div className="prose prose-invert max-w-none prose-headings:text-white prose-p:text-gray-300 prose-a:text-blue-400 prose-strong:text-white prose-code:text-green-400 prose-pre:bg-gray-900 prose-table:text-gray-300 prose-th:text-white prose-td:border-gray-700 prose-th:border-gray-700">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>
        {content}
      </ReactMarkdown>
    </div>
  )
}
