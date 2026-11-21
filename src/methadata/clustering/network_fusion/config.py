"""Domyślna konfiguracja K10 (lustro configs/network_fusion/snf.json; test pilnuje zgodności).
Parametry NIE są dopasowane do danych: to ustawienia wyjściowe do oceny."""
DEFAULT_CONFIG = {
    "similarity_version": "snf-v1(lexical-tfidf-v1+assets-jaccard-v1+dom-paths-jaccard-v1)",
    "views": {
        "text": {"method_id": "lexical-tfidf-v1"},
        "image": {"method_id": "assets-jaccard-v1"},
        "structure": {"method_id": "dom-paths-jaccard-v1"},
    },
    "extractors": ["assets-hash-extractor", "lexical-text-extractor", "dom-struct-extractor"],
    "subset": "complete",          # "complete" = tylko materiały ze wszystkimi widokami; "all" = wszystkie
    "neighbors": 5,                # K sąsiadów lokalnego jądra SNF (ograniczane do n-1)
    "iterations": 20,              # T
    "threshold": 0.5,              # próg krawędzi na fuzji przeskalowanej do [0,1] (podział przez maksimum)
    "scale": "local",              # skalowanie fuzji do [0,1]: local (wg najsilniejszych powiązań pary) | global (max)
    "min_view_mean": 0.2,          # bramka: średni wynik widoków pary (niezmierzony = 0) poniżej -> wynik obcięty pod próg
    "top_via": 3,                  # ile sąsiadów pośredniczących pokazać jako ślad krawędzi wzmocnionej
    "clusterer": "components",     # components (K01) | spectral (K06) | leiden (K05)
    "clusterer_config": {},
    # profil porównania z K09 (fuzja progowa): min_support odrębnych widoków i próg wsparcia widoku
    "fusion": {"min_support": 2, "coverage_min": 0.5, "support_min": 0.5},
}
