# Stan orkiestracji (2026-11-21)

Lokalny `main` po scaleniu W04 i E18. Pełny pytest zaliczył 310 testów i pominął 2 z powodu braku ffmpeg i próbki jQuery.

## Scalone do main
F01, E01, K01, F03, W01, X01, X03, X05, S01, F02 (+ujednolicony DerivedStore), X04 (+kanoniczny DerivedStore),
E02, E03, F05, F04, E05, R01, E04, E10, K02, E14, E13, X02, K09, E08, E07, W02, W03, F06, K05, W04, E18, poprawki testów (TFIX, TFIX2).

## W02 (backend)
Start: `METHADATA_DATA_DIR=var/methadata-data .venv/bin/python -m methadata_api` (port 8000; METHADATA_HOST/PORT/WORKERS), opis w backend/README.md.
Walidacja: `python contracts/http/check_conformance.py --base-url http://localhost:8000` → OK. Ograniczenia: jedyny profil analizy `demo` (baseline assets-jaccard, bez fuzji K09); URL bez pobierania (offline); kolejka w jednym procesie.

## W04 i E18

- W04 (`64a88f8`). Trzy scenariusze Playwright na Compose przeszły przed zatrzymaniem backendu i po jego przywróceniu. Podczas awarii główny scenariusz zgłosił HTTP 502, a test komunikatu w UI przeszedł. [Instrukcja i wyniki](../tests/web-e2e/README.md)
- E18 (`8bb01e5`). CLI przeliczyło siedem kombinacji dla każdego z trzech celów na lokalnym korpusie. Wynik zawiera bazę i 21 scenariuszy, zmiany krawędzi oraz dowody gotowe do odczytu przez R03. [Konfiguracja, instrukcja i wyniki](../configs/counterfactual/README.md)

## Następne (zależności już w main)
- R02 jest gotowe do przydzielenia po scaleniu W04 i F06. R03 można przydzielić po scaleniu R02.
- W03: `docker compose up --build` → http://localhost:5173 (backend 127.0.0.1:8000). F06: `python -m methadata.fusion_eval run ...` (raport fusion_eval.md z rekomendacją dla R02).
- Od razu: K03, K04, K06, K07, K13, E06, S04, S05, S10, E09, E11, E16, S02, E15, S06, S07, S09, E12, S03, K08, K15, K12, E17, K10, K14, K11, R04.

## Konwencje (obowiązkowe dla wykonawców)
- Adaptery: entry point `[project.entry-points."methadata.adapters"]`, test `discover()`; bez shimów `methadata_ext`.
- Artefakty pochodne: wyłącznie `methadata.ingest.store.DerivedStore` (F02).
- Dane rzeczywiste: `methadata.devtools.realdata` (`BACKUP`, `SCAM_LINKS`); żadnych ścieżek bezwzględnych w kodzie/testach.
- Extras do pełnych testów: `pip install -e '.[test,media,pdf,ocr,web,graph]'`.

## Znane luki do późniejszej integracji
- Runner F03 nie przekazuje komparatorom korpusu (ważony Jaccard E01, rare-motifs E14, IDF E02 bez modelu) — pełne wyniki tylko ścieżką `methadata.clustering.fusion run` / baseline.
- E10: heurystyka układu słaba na postach społecznościowych; E08: etykiety obrazów referencyjnych `verified=false`.
- R01: ścieżka TLS/internet nietestowana. X05: klatki wideo nietestowane (brak ffmpeg).

## Wznowienie
`scripts/orchestration/`: `gen.sh KARTA` (worktree z lokalnego main + prompt z WIADOMOSCI_DO_MODELI.md i `prompt.txt`),
`merge.sh KARTA BRANCH` (merge --no-ff, auto-suma konfliktu w pyproject z walidacją TOML, pip install, pytest), `mkwt.sh`.
