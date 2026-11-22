# Baza referencyjna osób i logotypów

Niewielka baza, na której system pokazuje dopasowanie wizualne („ta sama osoba publiczna” w kilku materiałach).
Rozmiar nie jest ważny: wystarczy 3–5 zdjęć.

## Jak dodać

- Plik na osobę: `Imie_Nazwisko.jpg` (też `.png`, `.webp`), albo
- podkatalog `Imie_Nazwisko/` z kilkoma zdjęciami tej samej osoby (lepsze dopasowanie).

Nazwa pliku lub katalogu (podkreślniki i myślniki zamieniane na spacje) staje się etykietą w interfejsie,
w widoku **Wspólne ślady** jako „Osoba publiczna (wizualnie)”.

Zdjęcie powinno przedstawiać twarz wyraźnie, bez dużego tekstu i grafiki nałożonej na twarz.

## Gdzie system szuka

1. katalog z `METHADATA_REFERENCE_DIR`,
2. `var/reference_figures/` (poza repozytorium, ignorowany przez git),
3. ten katalog (`data/reference_figures/`).

W Dockerze ten katalog jest podłączany do kontenera tylko do odczytu (`compose.yaml`).

## Zdjęcia w tym katalogu

W repozytorium są trzy zdjęcia na wolnych licencjach (Karol Nawrocki, Tomasz Sekielski, Rafał Brzoska) z opisem autorów i licencji w `SOURCES.md`.
Pozostałe pliki graficzne są domyślnie **ignorowane przez git** (zob. `.gitignore` obok), żeby nie opublikować przypadkiem materiałów
z zestawu organizatora, których nie wolno rozpowszechniać publicznie. Własne zdjęcia dodawaj tylko z prawami do ich użycia
(np. Wikimedia Commons z poprawnym przypisaniem), zapisz źródło w `SOURCES.md` i dopisz wyjątek w `.gitignore`.

Dopasowanie wizualne działa jako **sygnał pomocniczy**: wskazuje podobieństwo wyglądu, nie potwierdza tożsamości.
Wymaga OpenCLIP (`METHADATA_COLLECTION_MODELS=1`, extra `visual-embedding`) lub, awaryjnie, pHash.
