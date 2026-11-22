# Zgodność z wymaganiami wyzwania

Lista sprawdzona względem opisu zadania (rozdziały 3–6 i 9). Status **Spełnione** oznacza, że funkcja działa i ma dowód
(test albo przebieg ręczny opisany w kolumnie „Jak sprawdzić”). **Częściowo** i **Wymaga działania** opisują dokładnie, czego brakuje.

## 3. Oczekiwany rezultat

| Wymaganie | Status | Gdzie / dowód | Jak sprawdzić |
| --- | --- | --- | --- |
| Przyjmowanie materiałów: URL reklamy lub strony | Spełnione | Ekran **Nowa analiza** → adres lub domena; bezpieczny fetch, martwa domena zapisuje sam adres | `tests/web-e2e` (sam URL, martwa domena) |
| Zrzut ekranu, log komunikacji (HAR), plik multimedialny, PDF | Spełnione (media w trybie pełnym) | Import pliku; PNG/JPEG/WebP, HAR, MP4/WebM, MP3/WAV/OGG, PDF | README, „Wejścia analizy” |
| Dane ze zbioru, ZIP (także zagnieżdżone) | Spełnione | **Baza materiałów** → Dodaj materiały | `tests/backend/test_collection.py` |
| „Inna wskazówka” (nazwa produktu, osoba, słowa kluczowe) | Spełnione | Formularz **Wskazówka** na ekranie startowym, wyszukiwanie po treści materiałów | `test_material_text_is_searchable_by_keyword…`, `App.test.tsx` |
| Zbieranie tekstu, struktury stron, domen, przekierowań, linków | Spełnione | Ekstraktory `lexical`, `dom`, `url-har`, `extract-html`, `extract-code` | `docs/METHODOLOGY.md` §2 |
| Publiczne metadane domen i infrastruktury | Spełnione | DNS, TLS, RDAP z cache, tylko adresy publiczne | `tests/backend/test_enrichment.py` |
| Słowa kluczowe | Spełnione | Wyszukiwanie po treści, nazwy osób i marek jako wspólne ślady | j.w. |
| Tematyka | Częściowo | Schematy narracji (haczyk, obietnica, CTA), podsumowanie grupy; brak osobnej klasyfikacji tematów | METHODOLOGY §2–3 |
| Wizerunki osób (tekst) | Spełnione | `people-entities` (osoby, marki, produkty w tekście) | METHODOLOGY §2 |
| Wizerunki osób (obraz) | Spełnione | `reference_visual.py` + baza referencyjna: Karol Nawrocki, Tomasz Sekielski, Rafał Brzoska (wolne licencje, `SOURCES.md`); pHash zawsze, OpenCLIP w trybie pełnym | `test_bundled_reference_photos_match…` |
| Logotypy | Częściowo | Logotypy działają jak osoby referencyjne (dodaj obrazy do katalogu), ale nie ma osobnego detektora logotypów | j.w. |
| Cechy wizualne | Spełnione | SHA-256, pHash, ORB, OpenCLIP (tryb pełny), OCR | METHODOLOGY §2 |
| Zamiana na cechy porównywalne, duplikaty i warianty | Spełnione | Kanoniczny identyfikator, pHash, 5-gramy; duplikaty nie dublują węzłów | `test_backup_import_deduplicates…`, przebieg „re-import” |
| Metoda podobieństwa i grupowania | Spełnione | Fuzja ważona (grafiki, tekst, DOM), próg 0,5, pokrycie ≥ 0,5, wsparcie 2 rodzin; spójne składowe | METHODOLOGY §3 |
| Nowy materiał względem zbioru, potencjalne nowe kampanie | Spełnione | Raport importu: dołączył do grupy / nowa grupa / bez grupy | METHODOLOGY §6, `groupNote` w `CollectionScreen.tsx` |
| Prezentacja: klastry, graf, lista | Spełnione | Baza (grupy), Graf powiązań, Wspólne ślady, Oś czasu | `tests/web-e2e` |
| Przesłanki powiązań | Spełnione | Wynik na rodzinę, uzasadnienie, dowód z obu stron, scenariusze co-jeśli | Panel **Dowody** |
| Prosty interfejs graficzny | Spełnione | React + FastAPI | `docker compose up --build` |
| Dokumentacja metodyki | Spełnione | `docs/METHODOLOGY.md` | — |
| Niekompletne dane | Spełnione | „Za mało danych” zamiast zgadywania; URL-only, brak HTML, brak audio | `tests/web-e2e`, METHODOLOGY §4 |
| Bonus: wyszukiwanie w bibliotekach reklam | Częściowo | `methadata.monitor` (Meta Ad Library przez token, eksport JSON offline); przycisk **Skanuj źródło** w UI na eksporcie lokalnym | `tests/backend/test_monitor_scan.py` |
| Bonus: identyfikowanie nowych materiałów powiązanych z kampaniami | Częściowo | Monitor grupuje po płatniku i pokazuje nowe wpisy, ale nie zasila grafu kolekcji | METHODOLOGY §8 |

## 4. Wymagania formalne

| Wymaganie | Status | Dowód |
| --- | --- | --- |
| Działający demonstrator z GUI | Spełnione | README „Start (Docker)”; przycisk **Uruchom przykład** (syntetyczny zestaw: grupa {A, C}, B osobno) |
| Opis metodyki | Spełnione | `docs/METHODOLOGY.md` |
| Instrukcja instalacji, konfiguracji, uruchomienia i zależności | Spełnione | README (Docker, bez Dockera, tryb pełny, jury) |
| Kod w repozytorium Git | **Wymaga działania** | Wiele zmian i nowych plików jest jeszcze nieśledzonych: zatwierdź je przed oddaniem |
| Środowisko skonteneryzowane | Spełnione | `compose.yaml`; obraz lekki sprawdzony budowaniem; tryb pełny (modele, ffmpeg) to opcja budowania, nie sprawdzona w tej sesji |
| Zewnętrzne usługi z autoryzacją: opis dostępu i weryfikacja bez kluczy | Spełnione | OpenAI i Meta opisane w README; bez kluczy aplikacja działa (deterministyczne podsumowania, eksport offline) |
| Biblioteki, modele, usługi wraz z licencjami | Spełnione | Tabela w README |

## 5–6. Wymagania techniczne i walidacja

| Kryterium walidacji | Status | Dowód |
| --- | --- | --- |
| Skuteczność identyfikowania podobnych materiałów i wariantów | Spełnione | Demo {A, C} (0,90), przebieg k1–k3, korpus jury (README) |
| Poprawne przypisanie nowych materiałów do grup | Spełnione | Przebieg: nowy wariant dołączył do grupy |
| Niełączenie materiałów, które nie należą do kampanii | Spełnione | Demo: B z wynikami 0,15 i 0,20; kurs i pożyczka poza grupą |
| Odporność na duplikaty, niekompletne dane, różne formaty | Spełnione | Duplikaty, URL-only, HAR, PDF, ZIP w testach |
| Interpretowalność | Spełnione | Panel dowodów |
| Kompletność od wczytania do wyniku | Spełnione | `tests/web-e2e/full-flow.spec.ts` (4 testy) |
| Niewielka baza referencyjna osób | Spełnione | `data/reference_figures/` (3 osoby, źródła i licencje w `SOURCES.md`) |

## 8. Zasady korzystania z danych

| Zasada | Status |
| --- | --- |
| Materiały organizatora nie są publicznie rozpowszechniane | Spełnione: `BACKUP/`, `scam_links.*`, katalog organizatora i `var/` są w `.gitignore`; zdjęcia referencyjne także |
| Dodatkowe dane z podanym pochodzeniem | Spełnione: sekcja „Źródła danych” w METHODOLOGY i `data/reference_figures/SOURCES.md` |
| Dane organizatora nie trafiają do zewnętrznych usług bez wiedzy użytkownika | Spełnione: podsumowania OpenAI są domyślnie wyłączone (`METHADATA_ENABLE_LLM=1` włącza je świadomie), wzbogacanie domen i pobieranie stron opisano w README |

## Otwarte sprawy przed oddaniem

1. Zatwierdzić zmiany w Git (nieśledzone: m.in. `collection.py`, `enrichment.py`, `monitor.py`, `docs/`, `data/`).
2. Sprawdzić tryb pełny w Dockerze na maszynie z siecią (pobranie modeli), bo nie był uruchamiany tutaj.
3. Opcjonalnie: wygenerować token Meta Ad Library i uruchomić skan na żywo przez CLI.
