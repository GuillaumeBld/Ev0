'use client'

import { useEffect } from 'react'
import { AlertTriangle, RefreshCw } from 'lucide-react'

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string }
  reset: () => void
}) {
  useEffect(() => {
    console.error('Unhandled error:', error)
  }, [error])

  return (
    <div className="min-h-screen bg-gray-900 flex items-center justify-center p-4">
      <div className="max-w-md w-full bg-gray-800 rounded-xl p-8 text-center">
        <AlertTriangle className="w-12 h-12 text-red-400 mx-auto mb-4" />
        <h2 className="text-xl font-bold text-white mb-2">
          Une erreur est survenue
        </h2>
        <p className="text-gray-400 mb-6 text-sm">
          {error.message || "L'application a rencontre un probleme inattendu."}
        </p>
        <button
          onClick={reset}
          className="flex items-center justify-center gap-2 mx-auto px-6 py-2 bg-brand-600 hover:bg-brand-700 text-white rounded-lg transition-colors"
        >
          <RefreshCw className="w-4 h-4" />
          Reessayer
        </button>
      </div>
    </div>
  )
}
