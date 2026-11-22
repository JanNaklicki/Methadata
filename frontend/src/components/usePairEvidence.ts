import { useEffect, useState } from 'react'
import { api, ApiError } from '../api/client'
import type { Evidence } from '../api/types'

export function usePairEvidence(runId?: string, evidenceId?: string) {
  const [state, setState] = useState<{ key: string; evidence?: Evidence; error?: string }>()
  const [attempt, setAttempt] = useState(0)
  const key = runId && evidenceId ? JSON.stringify([runId, evidenceId, attempt]) : undefined

  useEffect(() => {
    if (!runId || !evidenceId || !key) return
    const request = new AbortController()
    api.getEvidence(runId, evidenceId, request.signal).then((evidence) => {
      if (!request.signal.aborted) setState({ key, evidence })
    }, (error: unknown) => {
      if (!request.signal.aborted) setState({
        key, error: error instanceof ApiError ? error.message : 'Sprawdź połączenie i spróbuj ponownie.',
      })
    })
    return () => request.abort()
  }, [key, runId, evidenceId])

  return {
    evidence: state?.key === key ? state?.evidence : undefined,
    error: state?.key === key ? state?.error : undefined,
    retry: () => setAttempt((count) => count + 1),
  }
}
