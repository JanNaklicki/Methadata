import { useEffect, useState } from 'react'
import { api, ApiError } from './api/client'
import type { Run } from './api/types'

export interface PollState {
  run: Run | null
  error: ApiError | null // błąd terminalny (np. 404)
  connectionLost: boolean // chwilowa utrata połączenia; odpytywanie trwa
}

/** Odpytuje API do stanu końcowego; zatrzymuje się przy odmontowaniu widoku. */
export function useRunPolling(runId: string, pollMs = 1500): PollState {
  const [state, setState] = useState<PollState>({ run: null, error: null, connectionLost: false })

  useEffect(() => {
    const ctrl = new AbortController()
    let timer: ReturnType<typeof setTimeout> | undefined
    setState({ run: null, error: null, connectionLost: false })

    const tick = async () => {
      let next = true
      try {
        const run = await api.getRun(runId, ctrl.signal)
        if (ctrl.signal.aborted) return
        setState({ run, error: null, connectionLost: false })
        next = run.status === 'queued' || run.status === 'running'
      } catch (e) {
        if (ctrl.signal.aborted) return
        if (e instanceof ApiError && (e.isNetwork || e.retryable)) {
          setState((s) => ({ ...s, connectionLost: true }))
        } else {
          setState({ run: null, error: e instanceof ApiError ? e : null, connectionLost: false })
          next = false
        }
      }
      if (next && !ctrl.signal.aborted) timer = setTimeout(tick, pollMs)
    }
    void tick()
    return () => {
      ctrl.abort()
      if (timer) clearTimeout(timer)
    }
  }, [runId, pollMs])

  return state
}
