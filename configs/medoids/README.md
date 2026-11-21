# K04 · Medoidy i centroidy

Adapter F03: `medoids-kmedoids-v1` (wymaga extras `density`: numpy, scikit-learn). Konfiguracja: `dataset_version`, `similarity_version`, `k` (domyślnie 2), `max_distance` (domyślnie 0.5), opcjonalnie `relation`, `method_id`.

k-medoids (PAM) działa na pełnej macierzy 1 - score; reprezentantem rodziny jest faktyczny materiał (medoid). Materiał, który k-medoids musiałby wymusić do rodziny mimo odległości > `max_distance`, dostaje status `noise` i dowód `forced_rejected`. Każde członkostwo wskazuje PairEvidence łączące z medoidem.

`build_centroid_result` (k-means na wektorach znormalizowanych L2; `vector_version` wymagane) to porównanie badawcze; wymaga wektorów podanych jawnie, nie jest adapterem F03. Reprezentant to materiał najbliższy centroidowi.

Uruchomienie: `python -m methadata.clustering.medoids --config configs/medoids/comparison.json`
