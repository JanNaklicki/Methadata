# K13 · Miękkie przypisania

Adapter F03: `soft-membership-v1` (extras `density`: numpy, scikit-learn). Wektory podaje się jawnie
(`config.vectors` lub `optional["vectors"]`: id materiału -> wektor) wraz z `vector_version`, `dataset_version`.

Konfiguracja: `algorithm` (`fcm` domyślnie | `gmm`), `components` (2), `fuzzifier` (2.0, tylko FCM), `pca_dims` (opcjonalna redukcja),
`min_samples_per_component` (3; próba < max(components+1, 3*components) -> status `insufficient_data` i `status_note=too_few_samples`),
`present_threshold` (0.3), `min_membership` (0.5).

Reguła prezentacji: materiał trafia do każdego klastra ze stopniem >= `present_threshold` (graniczny = kilka `cluster_ids`);
gdy max stopień < `min_membership` i nie ma drugiego klastra powyżej progu -> `noise`. Stopnie przynależności są wynikiem modelu
(FCM: wagi; GMM: posterior), nie pewnością rodziny. Stopnie i marża/entropia są w `SoftResult.evidence` / `.memberships`.

Stabilność: `stability(...)` mierzy zgodność par (indeks Randa twardych etykiet) wobec przebiegu odniesienia dla liczby składników i ułamka zbioru.
