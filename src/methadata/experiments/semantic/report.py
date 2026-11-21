"""Próbka kontrolna E06: parafraza odzyskuje pierwowzór; najwyżej oceniona para o wspólnym temacie i różnym przekazie.

Użycie: python -m methadata.experiments.semantic.report [--cache DIR]
Używa prawdziwego modelu, jeśli jest dostępny; inaczej drukuje status unsupported (bez wyników).
Wynik dotyczy WYŁĄCZNIE próbki kontrolnej (ręcznie ułożone akapity), nie danych źródłowych."""
from __future__ import annotations

import json
import sys

from .backend import BackendUnavailable, EmbeddingBackend, EmbeddingCache, SentenceTransformerBackend, cosine

ORIGINALS = [
    "Zarabiaj 5000 zł miesięcznie inwestując w złoto, bez wychodzenia z domu",
    "Ten krem na grzybicę stóp działa w tydzień bez recepty",
    "Lekarze nie chcą, żebyś znał ten sposób na schudnięcie 10 kilo w miesiąc",
]
PARAPHRASES = [  # parafraza i-tego pierwowzoru (inne słowa, to samo znaczenie; część po angielsku)
    "Earn five thousand zloty a month investing in gold from your couch",
    "Preparat na grzyba stóp pomaga po siedmiu dniach, recepta niepotrzebna",
    "Doctors hide this trick to lose ten kilos in four weeks",
]
SAME_TOPIC_DIFFERENT_MESSAGE = [  # wspólny temat (złoto), inny przekaz
    ("Zarabiaj 5000 zł miesięcznie inwestując w złoto, bez wychodzenia z domu",
     "Inwestowanie w złoto to ryzykowny zakład i możesz stracić oszczędności"),
    ("Ten krem na grzybicę stóp działa w tydzień bez recepty",
     "Grzybica stóp wymaga wizyty u lekarza, kremy bez recepty często nie wystarczają"),
]


def controlled(backend: EmbeddingBackend, cache_dir=None) -> dict:
    cache = EmbeddingCache(backend, cache_dir)
    vo, vp = cache.embed(ORIGINALS), cache.embed(PARAPHRASES)
    rec = []
    for i, p in enumerate(vp):
        sims = [cosine(p, o) for o in vo]
        rec.append({"paraphrase": PARAPHRASES[i], "recovered": ORIGINALS[max(range(len(vo)), key=lambda k: sims[k])],
                    "correct": max(range(len(vo)), key=lambda k: sims[k]) == i, "cosine": max(sims)})
    pairs = []
    for a, b in SAME_TOPIC_DIFFERENT_MESSAGE:
        va, vb = cache.embed([a, b])
        pairs.append({"a": a, "b": b, "cosine": cosine(va, vb)})
    pairs.sort(key=lambda r: -r["cosine"])
    return {"sample": "kontrolna (ręczna), nie dane źródłowe", "model": f"{backend.model_id}@{backend.revision}",
            "paraphrase_recovery": rec, "recovered_n": sum(r["correct"] for r in rec), "of": len(rec),
            "top_topic_pair": pairs[0] if pairs else None, "failure_case": "para o wspólnym temacie, ale przeciwnym przekazie "
            "może mieć cosinus zbliżony do parafrazy (embedding nie rozróżnia tezy i jej zaprzeczenia)"}


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    cache = argv[argv.index("--cache") + 1] if "--cache" in argv else None
    be = SentenceTransformerBackend(cache_dir=cache)
    try:
        out = controlled(be)
    except BackendUnavailable as e:
        print(json.dumps({"status": "unsupported", "reason": str(e), "model": be.model_id}, ensure_ascii=False))
        return 0
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
