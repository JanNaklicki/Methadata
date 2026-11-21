# K03 · DBSCAN, HDBSCAN i OPTICS

Adaptery F03 to `density-dbscan-v1`, `density-hdbscan-v1` i `density-optics-v1`. Konfiguracja klastrowania wymaga `dataset_version` i `similarity_version`. `relation` domyślnie wynosi `shared_template`. `method_id` wybiera jedną metodę porównywania. Przy kilku metodach w tej samej relacji trzeba wskazać ją jawnie.

## Uruchomienie

Polecenia wykonaj z katalogu repozytorium w aktywnym środowisku Pythona.

```sh
python -m pip install -e '.[test,density]'
python -m pytest tests/density
python -m methadata.clustering.density > density-control.json

# Oryginały F04, gdy lokalne BACKUP jest dostępne.
python -m methadata.ingest import --store var/density-store BACKUP
python -m methadata.clustering.density --store var/density-store > density-comparison.json
```

`--config` pozwala podać inny plik scenariuszy, domyślnie [comparison.json](comparison.json). Raport zawiera wersje i konfiguracje, `ClusterRun`, dowody per materiał oraz wejściowe `PairEvidence`. Jeśli pomiar obejmuje F04, najpierw sprawdza jego zamrożony korpus i wynik odniesienia.

## Macierz i dowody

W każdym scenariuszu uporządkowane identyfikatory kanoniczne wyznaczają kolejność symetrycznej macierzy `float64`, z zerową przekątną i odległością dokładnie `1.0 - score` poza przekątną. W przypadku kilku porównań tej samej pary i metody pierwszeństwo ma najwyższy wynik, a przy remisie leksykograficznie pierwszy identyfikator dowodu. Wynik `0` oznacza odległość `1`. Brak rekordu i `score=null` oznaczają brak porównania.

Najpierw odrzucamy materiały bez żadnego porównania. Przy niekompletnej macierzy usuwamy kolejno materiał z największą liczbą braków, przy remisie leksykograficznie pierwszy, aż pozostanie pełna macierz. Materiały odrzucone i ewentualny samotny wiersz mają status `insufficient_data`. Status `noise` oznacza, że wiersz uzyskał komplet porównań i nie otrzymał grupy według zastosowanej reguły gęstości. Udział szumu używa mianownika obejmującego materiały z pełną macierzą.

- DBSCAN zapisuje łącza w promieniu `eps` między członkami grupy, w tym co najmniej jednym rdzeniowym. Diagnostyka rozróżnia rdzeń i brzeg oraz pokazuje liczbę sąsiadów i odległość rdzeniową. Własny punkt wlicza się do `min_samples`. Przy `min_samples=1` samotna grupa jest singletonem. [DBSCAN](https://scikit-learn.org/stable/modules/generated/sklearn.cluster.DBSCAN.html)
- HDBSCAN stosuje wybór EOM. W wyniku dla grupy pokazujemy drzewo minimalnej rozpiętości wewnątrz jej członków, liczone według wzajemnej osiągalności `max(d(a,b), core(a), core(b))`. Drzewo streszcza lokalne wsparcie już przypisanej grupy. Diagnostyka zawiera sąsiada wyznaczającego odległość rdzeniową i siłę przynależności mierzoną przez model. [HDBSCAN](https://scikit-learn.org/stable/modules/generated/sklearn.cluster.HDBSCAN.html)
- OPTICS stosuje ekstrakcję Xi z korektą poprzednika. `ClusterRun.edges` zapisuje łącza z poprzednikami z tej samej płaskiej grupy. Diagnostyka zachowuje kolejność, osiągalność i poprzednika również dla materiałów poza wspólną grupą. Nieskończone odległości są zapisywane jako `null`. [OPTICS](https://scikit-learn.org/stable/modules/generated/sklearn.cluster.OPTICS.html)

## Wyniki na oryginalnych zapisach F04

Pomiar wykonano na zbiorze `backup-p1-p15@b190264f9b6c0841` za pomocą scikit-learn 1.9.1 i NumPy 2.5.5. Wszystkie 91 porównań między 14 materiałami używa tej samej wersji `assets-jaccard-v1` i relacji `shared_template`. J jest indeksem Jaccarda zbiorów par współklastrowanych, liczonym względem bazowego przebiegu tej samej metody. J względem F04 porównuje grupy z progiem `0.50` baseline. To pomiar zgodności z konfiguracją odniesienia.

| Profil | Grupy | Szum | J | J względem F04 |
| --- | --- | ---: | ---: | ---: |
| DBSCAN `eps=.50`, `min_samples=2` | p2/p7/p15 · p8/p11 | 9/14 | 1,00 | 1,00 |
| DBSCAN `eps=.10` lub `min_samples=3` | p2/p7/p15 | 11/14 | 0,75 | 0,75 |
| DBSCAN `eps=.87` | p2/p3/p4/p7/p15 · p8/p11 | 7/14 | 0,36 | 0,36 |
| HDBSCAN `min_cluster_size=2`, `min_samples=2` | p2/p7/p15 · p8/p11 · p3/p4 · p6/p10/p14 | 4/14 | 1,00 | 0,50 |
| HDBSCAN `min_cluster_size=3` lub `min_samples=3` | p2/p3/p4/p7/p15 · p6/p10/p14 | 6/14 | 0,50 | 0,21 |
| OPTICS `max_eps=1`, `xi=.05`, `min_cluster_size=2`, `min_samples=2` | p2/p7/p15 · p8/p11 · p3/p4 | 7/14 | 1,00 | 0,80 |
| OPTICS `max_eps=.10` lub `xi=.80` | p2/p7/p15 | 11/14 | 0,60 | 0,75 |
| OPTICS `min_samples=3` | p2/p3/p4/p6/p7/p10/p14/p15 | 6/14 | 0,14 | 0,10 |

Przy bazowych parametrach wszystkie metody połączyły p8 z p11. Dowód `pev-66b113b6a234b7e4` pokazuje 19 wspólnych hashy z 35, czyli podobieństwo `0.542857` i odległość `0.457143`. Każda para trójki p2/p7/p15 ma 14 wspólnych hashy z 14. OPTICS i HDBSCAN odzyskały też p3/p4, gdzie 2 z 9 hashy są wspólne (`pev-7a5fdfa61c6bfa9d`, podobieństwo `0.222222`). Przy `eps=.87` DBSCAN dołącza tę parę do p2/p7/p15 przez słabe powiązania graficzne.

Przypadek zawodności HDBSCAN to p6/p10/p14. Wszystkie trzy pary mają Jaccard `0`, a krawędzie drzewa pokazują odległość `1`. Ta grupa wymaga dodatkowych sygnałów lub ręcznej oceny. Rozdzielenie remisów zależy od kolejności kanonicznej i implementacji scikit-learn, której wersję zapisujemy. Zmiana parametrów OPTICS także prowadziła do połączenia różnych grup. Zamrożony pomiar mówi o zbieżności hashy obrazów. Ocena wspólnego szablonu i kampanii wymaga etykiet referencyjnych.

## Próbka kontrolna syntetyczna

`density-control-v1` zawiera gęstą parę AB, luźniejszą trójkę CDE, odległy X i nieporównywalne M. Przy bazowych parametrach wszystkie algorytmy zwróciły AB i CDE, szum X (1/6) oraz M jako `insufficient_data`. Zmiany parametrów na kontroli przedstawia tabela.

| Profil | Grupy | Szum | J względem bazy metody |
| --- | --- | ---: | ---: |
| DBSCAN `eps=.10` | AB | 4/6 | 0,25 |
| DBSCAN `eps=.87` | AB · CDE | 1/6 | 1,00 |
| DBSCAN `min_samples=3` | CDE | 3/6 | 0,75 |
| HDBSCAN `min_cluster_size=3` lub `min_samples=3` | bez grup | 6/6 | 0,00 |
| OPTICS `max_eps=.10` lub `xi=.80` | AB | 4/6 | 0,25 |
| OPTICS `min_samples=3` | ABCDEX | 0/6 | 0,27 |

Gęsta macierz wymaga pamięci kwadratowej w liczbie materiałów. Zachłanne usuwanie wierszy może pominąć porównywalne lokalnie grupy w niepełnych danych. Różne dane wejściowe i zmiany wersji pakietu wymagają powtórzenia pomiaru.
