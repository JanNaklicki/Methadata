"""E15: komparator zbieżności emisji w czasie. Wymaga cech S01 (delivery_start/stop); czas pobrania to tylko obserwacja."""
from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from methadata.contracts import AdapterKind, AdapterSpec, PairEvidence, Relation

from .series import VERSION, cfg, make_item, pair_model

METHOD_ID = "temporal-emission-v1"


class TemporalComparator:
    """Relacja campaign_candidate. Wspólny wzorzec + bliskie okresy -> wynik 0.5..1.0 (1.0 gdy okresy nakładają się);
    wspólny wzorzec daleko w czasie -> od 0.5 w dół do ~0.5; sam wspólny okres bez wzorca -> najwyżej coactivity_cap (0.2),
    bo zbieżność okresu nie jest dowodem wspólnej kampanii. Brak wiarygodnych dat po którejś stronie -> score=None."""
    method_id = METHOD_ID
    spec = AdapterSpec(id=METHOD_ID, kind=AdapterKind.comparator, version=VERSION,
                       description="Zbieżność okresów emisji (S01) i serie wzorca na domenach; strefa domyślna Europe/Warsaw")

    def __init__(self, config: Optional[Mapping[str, Any]] = None):
        self.config = cfg(config)

    def compare(self, a, b, features, *, run_id, optional=None) -> Sequence[PairEvidence]:
        (ca, oa), (cb, ob) = sorted([(a.canonical_id, a), (b.canonical_id, b)], key=lambda t: t[0])
        pair = (ca, cb)

        def none(why):
            return [PairEvidence(run_id=run_id, pair=pair, method_id=METHOD_ID, relation=Relation.campaign_candidate,
                                 score=None, explanation="brak podstaw: " + why)]
        ia, wa = make_item(oa, features.get(oa.observation_id, []), self.config)
        ib, wb = make_item(ob, features.get(ob.observation_id, []), self.config)
        if ia is None or ib is None:
            return none("; ".join(f"{n}: {w}" for n, i, w in ((ca, ia, wa), (cb, ib, wb)) if i is None))
        if not ia.refs or not ib.refs:
            return none("cechy dat bez odwołań do artefaktów")
        m = pair_model(ia, ib, self.config)
        tz = self.config["timezone"]
        if m["shared"]:
            why = (f"wspólny wzorzec ({len(m['shared'])}); okresy emisji nakładają się w {m['overlap']:.0%} krótszego"
                   if m["overlap"] > 0 else f"wspólny wzorzec; odstęp między okresami {m['gap_days']:.1f} dni")
            if ia.domain and ib.domain and ia.domain == ib.domain:
                why += "; ta sama domena (nie jest to seria na różnych domenach)"
        else:
            why = (f"tylko zbieżność okresu ({m['overlap']:.0%} krótszego), bez wspólnego wzorca - wynik ograniczony do "
                   f"{self.config['coactivity_cap']}; samo to nie dowodzi wspólnej kampanii")
        expl = (f"{why}. A: {ia.interval.to_dict()['start']}..{ia.interval.to_dict()['end']}"
                f"{' (bez daty końca, do obserwacji)' if ia.interval.censored else ''}; "
                f"B: {ib.interval.to_dict()['start']}..{ib.interval.to_dict()['end']}"
                f"{' (bez daty końca, do obserwacji)' if ib.interval.censored else ''}. Daty emisji z S01 "
                f"(strefa dla dat bez przesunięcia: {tz}); data zrzutu to tylko obserwacja.")
        return [PairEvidence(run_id=run_id, pair=pair, method_id=METHOD_ID, relation=Relation.campaign_candidate,
                             score=m["score"], evidence_refs={ca: ia.refs if ca == ia.canonical_id else ib.refs,
                                                              cb: ib.refs if cb == ib.canonical_id else ia.refs},
                             explanation=expl)]


def get_adapters() -> list[Any]:
    return [TemporalComparator()]
