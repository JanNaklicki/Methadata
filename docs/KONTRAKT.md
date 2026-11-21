# Kontrakt v1 (F01)

Typy: `src/methadata/contracts/models.py` (źródło prawdy). JSON Schema: `src/methadata/contracts/schemas/v1/*.json`
(generowane: `python -m methadata.contracts.schema`; test pilnuje zgodności).

## Instalacja i test

```bash
python3 -m venv .venv            # Python >= 3.10
.venv/bin/pip install -e '.[test]'
.venv/bin/pytest                  # test kontraktu: tests/contract/
.venv/bin/python scripts/make_contract_fixtures.py   # odtworzenie próbek (syntetycznych)
.venv/bin/python scripts/check_real_backup.py BACKUP # kontrola na rzeczywistych plikach (lokalny BACKUP/, poza gitem)
```

## Format referencji

`ArtifactRef = {artifact_id, locator?}`; locator (`kind`): `text_span{start,end}` (znaki, end wyłącznie),
`image_rect{x,y,width,height,frame_ms?}` (piksele; klatka dla wideo), `har_entry{index}`, `byte_range{start,end}`.
Relacja rodzic–dziecko: `Artifact.parent` (ArtifactRef; locator = miejsce dziecka w rodzicu) + `parent_relation`
(`archive_member`, `har_response`, `inline_in`, `referenced_by`, `derived_from`). Te same bajty w dwóch miejscach to dwa artefakty
(różne `origin`) o tym samym `sha256`. W `PairEvidence.evidence_refs` kluczem jest `canonical_id` strony. Odwołania między rekordami
sprawdza `Bundle` (nieznany artefakt, dowód obcego materiału, krawędź bez PairEvidence → błąd). `score` poza [0,1] → błąd; `null` = niewykonalne.

## Stabilne ID (`contracts/ids.py`)

| ID | Wyznaczane z |
|---|---|
| `observation_id` | `source_ref` + `input_kind` (reimport tego samego rekordu = to samo ID) |
| `canonical_id` | zbiór (rola, SHA-256) dla `landing_html`/`landing_screenshot`/`creative_image`/`har`/`video`/`audio`; sam URL → skrót URL. Nie zależy od domeny, nazw plików, kolejności ani zasobów pomocniczych |
| `artifact_id` | obserwacja + rola + SHA-256 + `origin` |
| `feature_id` | obserwacja + family + kind + extractor_version + wartość |
| `pair_evidence_id` | run + para (nieuporządkowana) + metoda + relacja |

URL: pełny w `Artifact.url`, do prezentacji `display_url` z zamaskowanymi identyfikatorami kliknięcia (`mask_url`; niezgodne → błąd).

## Dodanie modułu

1. Zaimplementuj interfejs z `contracts/interfaces.py` (`Importer.import_source`, `Extractor.extract`, `Comparator.compare`, `Clusterer.cluster`)
   i wystaw `spec = AdapterSpec(id=..., kind=..., version=...)`. ID może mieć myślniki (`source-qr-v1`).
2. Dodaj plik `<katalog_zadania>/methadata_ext/<nazwa_modułu>.py` (nazwa pythonowa, z podkreślnikami) z `ADAPTERS = [...]` lub `get_adapters()`.
   `methadata_ext` to pakiet namespace – bez `__init__.py`, bez edycji centralnej listy. Katalog zadania musi być na `sys.path`
   (instalacja `pip install -e` lub konfiguracja runnera). Alternatywa: entry point grupy `methadata.adapters` w `pyproject.toml` modułu.
3. Zależności: wymagane – `[project].dependencies`; opcjonalne (OCR, embeddingi, graf) – własne `[project.optional-dependencies]`
   i nazwy w `AdapterSpec.extras`. `discover(strict=False)` pomija moduły, którym brakuje opcjonalnej biblioteki. R02 scala extras.
4. Testy modułu w `tests/<modul>/`, konfiguracja lokalna w katalogu zadania. Przykład: `tests/fixtures/contract/ext_a|ext_b`.

## Opcjonalne wejścia grafowe i wektorowe

Moduł deklaruje je w `AdapterSpec.optional_inputs` (`OptionalInput{name, kind: vector|graph, model, dim}`); runner przekazuje je
w argumencie `optional` (słownik nazwa → obiekt). Bez nich moduł musi działać. Wektor: `Feature(value_type=vector, vector_ref={ref→artefakt vector_store, index, model, model_version, dim})` –
duże wektory nigdy inline. Graf: artefakt `graph_edges`.

## Próbki (`tests/fixtures/contract/`)

Wszystkie syntetyczne (`tags:["synthetic"]`, `source_ref=synthetic://…`, adres `example.invalid`), bez danych źródłowych:
`complete`, `url_only`, `duplicate`, `missing_resource`, `ui_demo` (3 materiały + 2 pary + ClusterRun dla UI), `invalid/*` (każdy musi być odrzucony).

## Praca równoległa (po scaleniu F01)

```bash
git worktree add ../Methadata-F02 -b codex/importer main   # po jednym worktree i branchu na kartę
```
Pierwszy przydział (PLAN_IMPLEMENTACJI.md): W01→F05, F02→R01, F03→K01, E01→F04. Każdy zaczyna od rewizji z zależnościami z pola „Wymaga”.
