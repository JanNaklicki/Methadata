# Stan orkiestracji (2026-11-21)

Lokalny `main` = `31fd7e9` (niepublikowany na origin). Pełny pytest: 282 passed, 2 skipped (brak ffmpeg, brak próbek jQuery).

## Scalone do main
F01, E01, K01, F03, W01, X01, X03, X05, S01, F02 (+ujednolicony DerivedStore), X04 (+kanoniczny DerivedStore),
E02, E03, F05, F04, E05, R01, E04, E10, K02, E14, E13, X02, K09, E08, E07, poprawki testów (TFIX, TFIX2).

## Przerwane
- **W02** (backend FastAPI) — branch `codex/web-api`, commit WIP `a5861af`, worktree `.claude/worktrees/W02`.
  Niezweryfikowane. Dokończyć: testy + `python contracts/http/check_conformance.py --base-url http://localhost:8000`.

## Następne (zależności już w main)
- Demo: W02 (dokończyć) → W03 (W02, F05) ; F06 (F04, K09, E02, E03) → W04, E18 → R02 → R03.
- Od razu: K03, K04, K05, K07, K13, E06, S04, S05, S10, E09, E11, E16, S02, E15, S06, S07, S09, E12, S03, K08, K15, K12, E17 (po F06), K10/K14 (po F06), K11 (po F06), K06 (po K05), R04.

## Konwencje (obowiązkowe dla wykonawców)
- Adaptery: entry point `[project.entry-points."methadata.adapters"]`, test `discover()`; bez shimów `methadata_ext`.
- Artefakty pochodne: wyłącznie `methadata.ingest.store.DerivedStore` (F02).
- Dane rzeczywiste: `methadata.devtools.realdata` (`BACKUP`, `SCAM_LINKS`); żadnych ścieżek bezwzględnych w kodzie/testach.
- Extras do pełnych testów: `pip install -e '.[test,media,pdf,ocr]'`.

## Znane luki do późniejszej integracji
- Runner F03 nie przekazuje komparatorom korpusu (ważony Jaccard E01, rare-motifs E14, IDF E02 bez modelu) — pełne wyniki tylko ścieżką `methadata.clustering.fusion run` / baseline.
- E10: heurystyka układu słaba na postach społecznościowych; E08: etykiety obrazów referencyjnych `verified=false`.
- R01: ścieżka TLS/internet nietestowana. X05: klatki wideo nietestowane (brak ffmpeg).

## Wznowienie
`scripts/orchestration/`: `gen.sh KARTA` (worktree z lokalnego main + prompt z WIADOMOSCI_DO_MODELI.md i `prompt.txt`),
`merge.sh KARTA BRANCH` (merge --no-ff, auto-suma konfliktu w pyproject z walidacją TOML, pip install, pytest), `mkwt.sh`.
