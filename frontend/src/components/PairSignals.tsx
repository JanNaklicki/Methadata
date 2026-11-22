import SignalBreakdown from './SignalBreakdown'
import { usePairEvidence } from './usePairEvidence'

export default function PairSignals({ runId, evidenceId }: { runId: string; evidenceId: string }) {
  const { evidence, error, retry } = usePairEvidence(runId, evidenceId)
  if (error) return <div className="signal-load">
    <p role="alert" className="muted">Nie udało się pobrać sygnałów. {error}</p>
    <button type="button" className="outline" onClick={retry}>Ponów pobieranie</button>
  </div>
  if (!evidence) return <p role="status" className="muted">Wczytywanie sygnałów…</p>
  if (!evidence.families?.length) return <p className="muted">Brak ocen poszczególnych sygnałów.</p>
  return <SignalBreakdown families={evidence.families} />
}
