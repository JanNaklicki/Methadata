"""Domyślna konfiguracja fuzji (lustro configs/fusion/fusion.json; test pilnuje zgodności).
Wagi i progi NIE są dopasowane do danych: to ustawienia wyjściowe do oceny (F06)."""
DEFAULT_CONFIG = {
    "similarity_version": "fusion-v1(assets-jaccard-v1+lexical-tfidf-v1)",
    "threshold": 0.5,
    "coverage_min": 0.5,
    "min_support": 2,
    "families": {
        "asset": {"method_id": "assets-jaccard-v1", "weight": 0.5, "origin": "asset", "support_min": 0.5},
        "text": {"method_id": "lexical-tfidf-v1", "weight": 0.5, "origin": "content-text", "support_min": 0.3},
    },
}
