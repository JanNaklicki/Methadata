# E18 · Co podtrzymuje połączenie

E18 przelicza graf K09 dla wskazanego materiału. W każdym scenariuszu usuwa z zamrożonego wejścia F06 wyniki porównań wyłączonych rodzin tylko dla par zawierających ten materiał. Wagi rodzin, mianownik pokrycia, progi, minimalne wsparcie i seed pochodzą z tej samej konfiguracji. Zestawy są uporządkowane rosnąco według liczności, a remisy według nazw rodzin.

## Uruchomienie na zaimportowanym korpusie

```sh
python -m methadata.experiments.counterfactual run \
  --store "$STORE" \
  --baseline-config configs/baseline/baseline.json \
  --eval-config configs/fusion/eval.json \
  --config configs/counterfactual/e18.json \
  --out "$E18_OUT"
```

`STORE` wskazuje magazyn utworzony przez `python -m methadata.ingest import` z lokalnych plików. `E18_OUT` wskazuje katalog na `counterfactual.json` i `counterfactual.md`. Domyślny profil `kampania` używa rodzin `asset`, `dom` i `text` z równymi wagami, progu podobieństwa 0,5, minimalnego pokrycia 0,5 i wsparcia dwóch źródeł. Cele wskazuje `targets` w konfiguracji E18.

## Dane dla R03

`FamilySwitch(Inputs, fusion_config)` współdzieli zamrożone dowody, a wyniki scenariuszy przechowuje w pamięci według pary materiał i zestaw wyłączonych rodzin. `compare(material, disabled)` zwraca różnice względem bazowego przebiegu. `view(material, disabled)` zwraca przypisanie, bezpośrednie krawędzie i najkrótsze ścieżki do członków grupy, z którymi brak bezpośredniej krawędzi. Remisy ścieżek rozstrzyga kolejność identyfikatorów kanonicznych.

W JSON-ie `targets[material_id].baseline_view` przechowuje widok bazowy, a `scenarios[].view` odpowiednik dla scenariusza. Aby pokazać przyczyny najmniejszej zmiany, dopasuj `minimal_change.minimal[].run_id` do `scenarios[].run_id` i odczytaj `edge_changes`. Pola `separated_member_ids` i `joined_member_ids` wskazują, których członków grupy dotyczy zmiana. Każde przejście ma identyfikator dowodu fuzji i listę użytych dowodów rodzin. Oba rodzaje identyfikatorów rozwiązują się w mapie `pair_evidence`. Wersje i pełne rekordy przebiegów odczytuje się z `cluster_runs[run_id]`.

`was` i `now` przechowują pokrycie, ważone podobieństwo (`fused`), status bramki i wynik dla klasteryzacji (`score`). Przyczyna utraty krawędzi wynika z pierwszej niespełnionej bramki K09. `brak_zmiany` oznacza zachowany skład grup i zestaw akceptowanych krawędzi. Zmiany parametrów zachowanych krawędzi są w `edge_changes`, a zmiany porównań poza grafem w `comparison_changes`.

## Próbka kontrolna

```sh
PYTHONPATH=src python -m pytest -q tests/experiments_counterfactual
PYTHONPATH=src python - <<'PY'
import runpy

sample = runpy.run_path("tests/experiments_counterfactual/test_counterfactual.py")
print(sample["control_report"]()["markdown"])
PY
```

Wersja danych `e18-control-v1`, seed 19, wagi 1,0, próg podobieństwa 0,6, próg wsparcia każdej rodziny 0,3, minimalne pokrycie 0,5 i wsparcie dwóch źródeł. Bazowy przebieg `run-fusion-4e77b1152bc0` przypisał A, B, C i D do jednej grupy. A łączyło się bezpośrednio z B i D, a z C przez ścieżkę A–B–C. Test rozwiązuje odwołania do źródłowego PairEvidence na każdym z tych przejść.

| Wyłączenia dla A | ClusterRun | Wynik |
|---|---|---|
| asset | `run-fusion-c5e3736ae364` | Krawędź A–B znika, podobieństwo spada z 0,6633 do 0,52. Grupa się utrzymuje przez D. |
| dom | `run-fusion-14aaca55ab67` | Krawędź A–D znika, pokrycie spada z 2/3 do 1/3 przy podobieństwie 0,9. Grupa się utrzymuje. |
| text | `run-fusion-d84558c875eb` | Krawędź A–D znika z powodu pokrycia. Grupa się utrzymuje. |
| asset + dom | `run-fusion-1025139bfd01` | A opuszcza grupę. |
| asset + text | `run-fusion-c31f98e81300` | A opuszcza grupę. |
| dom + text | `run-fusion-baccb3cf1c83` | A opuszcza grupę. |
| asset + dom + text | `run-fusion-11bf4293e680` | A opuszcza grupę. B, C i D pozostają razem. |

Wszystkie trzy zestawy dwuelementowe są minimalne dla A. Niepowiązany materiał E ma status `insufficient_data` w każdym z siedmiu scenariuszy. W oddzielnym teście z progiem pokrycia 0,3 usunięcie słabego wyniku `asset` z porównania 0,1/0,8/0,8 tworzy krawędź. Usunięcie następnej rodziny ją blokuje, ponieważ do grupowania potrzeba dwóch wspierających źródeł.

## Pomiar lokalny i ograniczenia

Wersja korpusu `backup-p1-p15@b190264f9b6c0841`. Przebieg bazowy `run-fusion-07577d9f9ad4` był identyczny z profilem `kampania` F06 i dał trzy grupy z pięcioma krawędziami. CLI zapisało 22 wersje przebiegów, po siedem scenariuszy dla każdego z trzech celów oraz bazę. Dwa cele wymagały wyłączenia dowolnych dwóch rodzin i utraciły krawędzie na bramce pokrycia. Dla trzeciego celu wystarczyło wyłączenie samego DOM albo tekstu, co obniżyło liczbę wspierających źródeł do jednego. Próbka lokalna zawiera bezpośrednie krawędzie dla wszystkich par w grupach bazowych. Ścieżkę pośrednią, spadek podobieństwa i przypadek zyskania krawędzi zweryfikowano na próbce kontrolnej.

Pomiar korzysta z tego samego lokalnego korpusu i ustawień co F06. Uogólnienie na zewnętrzne dane wymaga osobnej oceny. Test sprawdza efekty wyłączenia dowodów w zamrożonym grafie. Ponowne zebranie materiału lub przeliczenie statystyk ekstrakcji całego korpusu dałoby inny eksperyment.
