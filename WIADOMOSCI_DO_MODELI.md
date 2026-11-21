# Wiadomości do modeli · Kolekcjoner Fejków

Każdy blok tekstowy to gotowa wiadomość do jednego czatu z dostępem do repozytorium. Skopiuj cały blok. Pierwsza część prowadzi do działającego frontendu i backendu, druga obejmuje dalsze eksperymenty i źródła danych.

**Gdzie zacząć.** Przy przygotowywaniu tej listy lokalny branch `codex/contract` miał rozpoczętą implementację F01. Jeśli jeden z modeli już nad nią pracuje, pozostaw F01 w tym czacie i zacznij wysyłać nowe wiadomości od tury 1 po scaleniu F01. Karty znajdują się w lokalnym `main`. Modele pracujące w osobnych klonach potrzebują dostępu do tej samej rewizji.

**Równolegle** oznacza osobny czat, branch i worktree dla każdej wiadomości. Tury w pierwszej części zakładają do czterech modeli i priorytet działającego podglądu. Gdy zwolni się model, możesz od razu wydać następną kartę, której wymienione zależności są już w `main`. Testy i commit na branchu kończą pracę wykonawcy. Scalenia do wspólnego lokalnego `main` wykonuj kolejno.

## Krótka kolejka do demo

| Tura | Wiadomości do wysłania | Wynik |
|---|---|---|
| 0 | F01 | Wspólny kontrakt dla wszystkich modeli |
| 1 | W01, F02, F03, E01 równolegle | OpenAPI, importer, runner i pierwsza metoda |
| 2 | F05, R01, K01, X01 równolegle | Frontend, przyjęcie zgłoszenia, graf i parser HTML |
| 3 | F04, E02, E03 równolegle | Odtworzenie bazowych grup oraz cechy tekstu i DOM |
| 4 | W02, K09 równolegle | Backend FastAPI i łączenie dowodów |
| 5 | W03, F06 równolegle | Podgląd jednym poleceniem i ocena fuzji |
| 6 | W04, E18 równolegle | Test przeglądarkowy i wyłączanie dowodów |
| 7 | R02 | Scenariusz jury na rzeczywistych reklamach |
| 8 | R03 | Interaktywny pokaz wpływu każdego rodzaju dowodu |

**Wiadomość do scalenia.** Po odbiorze zadania wklej poniższy blok w tym samym czacie, który oddał commit. Uruchamiaj jeden taki prompt naraz.

```text
Zintegruj ukończone i przetestowane zadanie z tego czatu z lokalnym main. Użyj brancha i commita podanych w swoim podsumowaniu. Sprawdź, czy zmiany są już scalone. Użyj osobnego czystego worktree dla integracji lub istniejącego czystego worktree main. Zachowaj prace innych modeli. Uruchom testy karty na zintegrowanej rewizji i popraw ewentualne regresje w jej zakresie. Podaj ID karty, SHA lokalnego main i wynik testów. Publikację zdalną zostaw osobie koordynującej.
```


## Tura 0 · Jedna wiadomość

### F01 · Kontrakt, próbki i szkielet projektu

```text
Wykonaj F01 z karty zadania/F01.md w repozytorium Methadata. Kontynuuj na istniejącym branchu codex/contract. Wykorzystaj dotychczasowe zmiany tego zadania.
Dokończ wspólny kontrakt danych, walidację, sposób odkrywania adapterów i próbki do pracy równoległej. Najpierw sprawdź istniejącą implementację.
Zrealizuj kryteria odbioru z karty i uruchom testy. Zacommituj własne zmiany. Podaj branch, SHA, polecenie weryfikacji, wynik i ewentualne ograniczenia.
```

## Tura 1 · Równolegle

### W01 · Kontrakt HTTP dla backendu i frontendu

Można wysłać po scaleniu F01.

```text
Wykonaj W01 z karty zadania/W01.md w repozytorium Methadata. Pracuj w osobnym worktree na branchu codex/web-contract, opartym na lokalnym main zawierającym F01.
Przygotuj kontrakt OpenAPI i przykłady odpowiedzi dla przesłania materiału, uruchomienia analizy, sprawdzania stanu i otwierania dowodów. Frontend musi móc powstać na tych przykładach.
Zrealizuj kryteria odbioru z karty i uruchom testy. Zacommituj własne zmiany. Podaj branch, SHA, polecenie weryfikacji, wynik i ewentualne ograniczenia.
```

### F02 · Importer, pełny inwentarz i deduplikacja

Można wysłać po scaleniu F01.

```text
Wykonaj F02 z karty zadania/F02.md w repozytorium Methadata. Pracuj w osobnym worktree na branchu codex/import, opartym na lokalnym main zawierającym F01.
Zaimplementuj importer materiałów z manifestem, deduplikacją i pochodzeniem każdego pliku. Zweryfikuj powtórny import i alias p1/p2.
Zrealizuj kryteria odbioru z karty i uruchom testy. Zacommituj własne zmiany. Podaj branch, SHA, polecenie weryfikacji, wynik i ewentualne ograniczenia.
```

### F03 · Runner eksperymentów i ocena par

Można wysłać po scaleniu F01.

```text
Wykonaj F03 z karty zadania/F03.md w repozytorium Methadata. Pracuj w osobnym worktree na branchu codex/benchmark, opartym na lokalnym main zawierającym F01.
Zbuduj runner i CLI oraz raport oceny par. Na małym ręcznie policzonym przykładzie pokaż, że metryki poprawnie obsługują brak decyzji.
Zrealizuj kryteria odbioru z karty i uruchom testy. Zacommituj własne zmiany. Podaj branch, SHA, polecenie weryfikacji, wynik i ewentualne ograniczenia.
```

### E01 · Identyczne zasoby i ważony Jaccard

Można wysłać po scaleniu F01.

```text
Wykonaj E01 z karty zadania/E01.md w repozytorium Methadata. Pracuj w osobnym worktree na branchu codex/exp-assets, opartym na lokalnym main zawierającym F01.
Zaimplementuj dokładny i ważony Jaccard dla zbiorów hashy grafik. Zwróć PairEvidence ze wspólnymi plikami i przetestuj parę z czterema wspólnymi grafikami na osiemnaście różnych.
Zrealizuj kryteria odbioru z karty i uruchom testy. Zacommituj własne zmiany. Podaj branch, SHA, polecenie weryfikacji, wynik i ewentualne ograniczenia.
```

## Tura 2 · Równolegle

### F05 · Frontend React i widok dowodów

Można wysłać po scaleniu W01.

```text
Wykonaj F05 z karty zadania/F05.md w repozytorium Methadata. Pracuj w osobnym worktree na branchu codex/demo, opartym na lokalnym main zawierającym W01.
Zbuduj osobny frontend React + TypeScript + Vite zgodny z OpenAPI. Obsłuż plik, URL, gotowy przykład, śledzenie przebiegu i otwieranie dowodów. Domyślnie korzystaj z realnego API.
Zrealizuj kryteria odbioru z karty i uruchom testy. Zacommituj własne zmiany. Podaj branch, SHA, polecenie weryfikacji, wynik i ewentualne ograniczenia.
```

### R01 · Przyjęcie rzeczywistego zgłoszenia i pozyskanie strony

Można wysłać po scaleniu F02.

```text
Wykonaj R01 z karty zadania/R01.md w repozytorium Methadata. Pracuj w osobnym worktree na branchu codex/intake, opartym na lokalnym main zawierającym F02.
Obsłuż rzeczywiste zgłoszenia plikowe i URL oraz zachowanie obserwacji przy błędzie pobrania.
Zrealizuj kryteria odbioru z karty i uruchom testy. Zacommituj własne zmiany. Podaj branch, SHA, polecenie weryfikacji, wynik i ewentualne ograniczenia.
```

### K01 · Graf progowy i składowe spójne

Można wysłać po scaleniu F01.

```text
Wykonaj K01 z karty zadania/K01.md w repozytorium Methadata. Pracuj w osobnym worktree na branchu codex/cluster-components, opartym na lokalnym main zawierającym F01.
Zbuduj klastry jako spójne składowe grafu progowego. Pokaż, jak pojedyncza słaba krawędź zmienia wynik.
Zrealizuj kryteria odbioru z karty i uruchom testy. Zacommituj własne zmiany. Podaj branch, SHA, polecenie weryfikacji, wynik i ewentualne ograniczenia.
```

### X01 · HTML, DOM, formularze i zawartość MHTML

Można wysłać po scaleniu F01.

```text
Wykonaj X01 z karty zadania/X01.md w repozytorium Methadata. Pracuj w osobnym worktree na branchu codex/extract-html, opartym na lokalnym main zawierającym F01.
Wydobądź z HTML i MHTML tekst, DOM, formularze i zasoby osadzone. Każdy ślad powiąż z lokalizacją w oryginale.
Zrealizuj kryteria odbioru z karty i uruchom testy. Zacommituj własne zmiany. Podaj branch, SHA, polecenie weryfikacji, wynik i ewentualne ograniczenia.
```

## Tura 3 · Równolegle

### F04 · Integracja i odtworzenie bazowego pomiaru

Można wysłać po scaleniu F02, F03, E01, K01.

```text
Wykonaj F04 z karty zadania/F04.md w repozytorium Methadata. Pracuj w osobnym worktree na branchu codex/baseline, opartym na lokalnym main zawierającym F02, F03, E01, K01.
Połącz importer, runner, Jaccarda i składowe spójne. Na korpusie zweryfikuj 14 unikalnych stron, 91 par i grupy p2/p7/p15 oraz p8/p11 przy progu 0,5.
Zrealizuj kryteria odbioru z karty i uruchom testy. Zacommituj własne zmiany. Podaj branch, SHA, polecenie weryfikacji, wynik i ewentualne ograniczenia.
```

### E02 · Podobieństwo tekstu TF-IDF

Można wysłać po scaleniu X01.

```text
Wykonaj E02 z karty zadania/E02.md w repozytorium Methadata. Pracuj w osobnym worktree na branchu codex/exp-lexical, opartym na lokalnym main zawierającym X01.
Usunąć menu i skrypty. TF-IDF słów oraz znakowych n-gramów + cosinus, osobno dla tytułu, korpusu i wezwania do działania.
Zrealizuj kryteria odbioru z karty i uruchom testy. Zacommituj własne zmiany. Podaj branch, SHA, polecenie weryfikacji, wynik i ewentualne ograniczenia.
```

### E03 · Podobieństwo struktury DOM

Można wysłać po scaleniu X01.

```text
Wykonaj E03 z karty zadania/E03.md w repozytorium Methadata. Pracuj w osobnym worktree na branchu codex/exp-dom, opartym na lokalnym main zawierającym X01.
Zastąpić tekst i zmienne identyfikatory znacznikami, porównać zbiory ścieżek tagów i kolejność sekcji. Wariant z odległością edycyjną sekwencji.
Zrealizuj kryteria odbioru z karty i uruchom testy. Zacommituj własne zmiany. Podaj branch, SHA, polecenie weryfikacji, wynik i ewentualne ograniczenia.
```

## Tura 4 · Równolegle

### W02 · Backend FastAPI i uruchamianie analiz

Można wysłać po scaleniu W01, F04, R01.

```text
Wykonaj W02 z karty zadania/W02.md w repozytorium Methadata. Pracuj w osobnym worktree na branchu codex/web-api, opartym na lokalnym main zawierającym W01, F04, R01.
Podłącz FastAPI do tego samego silnika co CLI. Zaimplementuj zlecanie analiz, trwałe wyniki i dowody oraz działający przykład bez prywatnego korpusu.
Zrealizuj kryteria odbioru z karty i uruchom testy. Zacommituj własne zmiany. Podaj branch, SHA, polecenie weryfikacji, wynik i ewentualne ograniczenia.
```

### K09 · Fuzja niezależnych rodzin dowodów

Można wysłać po scaleniu F04, E02.

```text
Wykonaj K09 z karty zadania/K09.md w repozytorium Methadata. Pracuj w osobnym worktree na branchu codex/cluster-fusion, opartym na lokalnym main zawierającym F04, E02.
Połącz wyniki niezależnych rodzin cech. Rozdziel podobieństwo i pokrycie oraz uwzględnij wspólne pochodzenie dowodów.
Zrealizuj kryteria odbioru z karty i uruchom testy. Zacommituj własne zmiany. Podaj branch, SHA, polecenie weryfikacji, wynik i ewentualne ograniczenia.
```

## Tura 5 · Równolegle

### W03 · Start obu usług jednym poleceniem

Można wysłać po scaleniu W02, F05.

```text
Wykonaj W03 z karty zadania/W03.md w repozytorium Methadata. Pracuj w osobnym worktree na branchu codex/web-preview, opartym na lokalnym main zawierającym W02, F05.
Zapewnij start frontendu i backendu przez docker compose up --build. Na czystym checkoutcie otwórz localhost:5173, uruchom przykład i sprawdź trwałość jego wyniku po restarcie.
Zrealizuj kryteria odbioru z karty i uruchom testy. Zacommituj własne zmiany. Podaj branch, SHA, polecenie weryfikacji, wynik i ewentualne ograniczenia.
```

### F06 · Ocena fuzji na rzeczywistym korpusie

Można wysłać po scaleniu F04, K09, E02, E03.

```text
Wykonaj F06 z karty zadania/F06.md w repozytorium Methadata. Pracuj w osobnym worktree na branchu codex/fusion, opartym na lokalnym main zawierającym F04, K09, E02, E03.
Zmierz wkład tekstu, DOM i grafik na wspólnym korpusie. Pokaż, które krawędzie i grupy zmieniają się po wyłączeniu rodziny.
Zrealizuj kryteria odbioru z karty i uruchom testy. Zacommituj własne zmiany. Podaj branch, SHA, polecenie weryfikacji, wynik i ewentualne ograniczenia.
```

## Tura 6 · Równolegle

### W04 · Test przeglądarkowy pełnego podglądu

Można wysłać po scaleniu W03.

```text
Wykonaj W04 z karty zadania/W04.md w repozytorium Methadata. Pracuj w osobnym worktree na branchu codex/web-e2e, opartym na lokalnym main zawierającym W03.
Napisz i uruchom test Playwright, który w prawdziwej przeglądarce przechodzi od zgłoszenia do dowodu przez działające API.
Zrealizuj kryteria odbioru z karty i uruchom testy. Zacommituj własne zmiany. Podaj branch, SHA, polecenie weryfikacji, wynik i ewentualne ograniczenia.
```

### E18 · Co podtrzymuje połączenie

Można wysłać po scaleniu F06.

```text
Wykonaj E18 z karty zadania/E18.md w repozytorium Methadata. Pracuj w osobnym worktree na branchu codex/exp-counterfactual, opartym na lokalnym main zawierającym F06.
Wyłączaj rodziny cech i przeliczaj grupy. Zwróć najmniejszą kombinację zmieniającą przynależność oraz przyczynę zmiany.
Zrealizuj kryteria odbioru z karty i uruchom testy. Zacommituj własne zmiany. Podaj branch, SHA, polecenie weryfikacji, wynik i ewentualne ograniczenia.
```

## Tura 7 · Jedna wiadomość

### R02 · Integracja demonstratora dla jury

Można wysłać po scaleniu W04, F06.

```text
Wykonaj R02 z karty zadania/R02.md w repozytorium Methadata. Pracuj w osobnym worktree na branchu codex/demo-release, opartym na lokalnym main zawierającym W04, F06.
Połącz sprawdzony podgląd webowy i konfigurację fuzji w profilu jury. Przejdź od dodania p15 do dwustronnych dowodów przy p2/p7.
Zrealizuj kryteria odbioru z karty i uruchom testy. Zacommituj własne zmiany. Podaj branch, SHA, polecenie weryfikacji, wynik i ewentualne ograniczenia.
```

## Tura 8 · Jedna wiadomość

### R03 · Interaktywny pokaz wyłączania dowodów

Można wysłać po scaleniu R02, E18.

```text
Wykonaj R03 z karty zadania/R03.md w repozytorium Methadata. Pracuj w osobnym worktree na branchu codex/demo-explain, opartym na lokalnym main zawierającym R02, E18.
Dodaj w przeglądarce przełączanie rodzin śladów i porównanie przeliczonego wyniku z bazowym. Zweryfikuj cały przebieg przez HTTP.
Zrealizuj kryteria odbioru z karty i uruchom testy. Zacommituj własne zmiany. Podaj branch, SHA, polecenie weryfikacji, wynik i ewentualne ograniczenia.
```

## Dalsze źródła i metody

Poniższe grupy zakładają dostępność kart z pierwszej części. Możesz je przydzielać wcześniej po spełnieniu warunku nad konkretnym promptem. Wiadomości w jednej grupie są niezależne. Przy ograniczonej liczbie modeli bierz po jednej na wolny czat. Dostępność danych lub wag oceń według karty, szczególnie dla audio, emisji i uczenia na ocenionych parach.

## Eksperymenty 1 · Równolegle

### E05 · Bliskie kopie tekstu i wyszukiwanie MinHash

Można wysłać po scaleniu X01.

```text
Wykonaj E05 z karty zadania/E05.md w repozytorium Methadata. Pracuj w osobnym worktree na branchu codex/exp-near-copy, opartym na lokalnym main zawierającym X01.
Jaccard kolejnych grup słów (shingles) i miara zawierania krótszego tekstu w dłuższym. MinHash/LSH jako tańsze wyszukiwanie kandydatów przy większym zbiorze.
Zrealizuj kryteria odbioru z karty i uruchom testy. Zacommituj własne zmiany. Podaj branch, SHA, polecenie weryfikacji, wynik i ewentualne ograniczenia.
```

### E06 · Podobieństwo semantyczne akapitów

Można wysłać po scaleniu X01.

```text
Wykonaj E06 z karty zadania/E06.md w repozytorium Methadata. Pracuj w osobnym worktree na branchu codex/exp-semantic, opartym na lokalnym main zawierającym X01.
Wielojęzyczne embeddingi akapitów, cosinus i zestawienie najlepiej dopasowanych fragmentów. Osobno przetestować tytuł, obietnicę i wezwanie do działania.
Zrealizuj kryteria odbioru z karty i uruchom testy. Zacommituj własne zmiany. Podaj branch, SHA, polecenie weryfikacji, wynik i ewentualne ograniczenia.
```

### E07 · Schemat narracji i role bohaterów

Można wysłać po scaleniu X01.

```text
Wykonaj E07 z karty zadania/E07.md w repozytorium Methadata. Pracuj w osobnym worktree na branchu codex/exp-narrative-slots, opartym na lokalnym main zawierającym X01.
Opisać scenariusz polami „kto”, „co się wydarzyło”, „obietnica”, „produkt”, „wezwanie”. Ekstrakcja regułami lub modelem z cytatem do każdego pola. Porównać układ ról po zamianie nazw własnych.
Zrealizuj kryteria odbioru z karty i uruchom testy. Zacommituj własne zmiany. Podaj branch, SHA, polecenie weryfikacji, wynik i ewentualne ograniczenia.
```

### E08 · Osoby, marki i produkty

Można wysłać po scaleniu X01.

```text
Wykonaj E08 z karty zadania/E08.md w repozytorium Methadata. Pracuj w osobnym worktree na branchu codex/exp-people-entities, opartym na lokalnym main zawierającym X01.
Słownik aliasów kilku osób, marek i produktów + rozpoznawanie nazw w HTML/OCR. Zapisać rolę wzmianki.
Zrealizuj kryteria odbioru z karty i uruchom testy. Zacommituj własne zmiany. Podaj branch, SHA, polecenie weryfikacji, wynik i ewentualne ograniczenia.
```

### E14 · Rzadkie kombinacje śladów

Można wysłać po scaleniu E01, E02, E03.

```text
Wykonaj E14 z karty zadania/E14.md w repozytorium Methadata. Pracuj w osobnym worktree na branchu codex/exp-rare-motifs, opartym na lokalnym main zawierającym E01, E02, E03.
Graf dwudzielny materiał–ślad. Wyszukiwać powtarzalne kombinacje rzadkiego zasobu, elementu formularza i fragmentu tekstu. Ważyć kombinację względem częstości składników.
Zrealizuj kryteria odbioru z karty i uruchom testy. Zacommituj własne zmiany. Podaj branch, SHA, polecenie weryfikacji, wynik i ewentualne ograniczenia.
```

### E17 · Uczenie podobieństwa na ocenionych parach

Można wysłać po scaleniu F03, F06.

```text
Wykonaj E17 z karty zadania/E17.md w repozytorium Methadata. Pracuj w osobnym worktree na branchu codex/exp-learned-pairs, opartym na lokalnym main zawierającym F03, F06.
Po zgromadzeniu ocen par nauczyć regresję logistyczną na jawnych podobieństwach i wskaźnikach braków. Aktywne uczenie podsuwa analitykowi niepewne pary.
Zrealizuj kryteria odbioru z karty i uruchom testy. Zacommituj własne zmiany. Podaj branch, SHA, polecenie weryfikacji, wynik i ewentualne ograniczenia.
```

### K02 · Aglomeracja average-link i complete-link

Można wysłać po scaleniu F04.

```text
Wykonaj K02 z karty zadania/K02.md w repozytorium Methadata. Pracuj w osobnym worktree na branchu codex/cluster-linkage, opartym na lokalnym main zawierającym F04.
Aglomeracja average-link i complete-link na odległościach 1 − podobieństwo. Ciąć dendrogram na wybranym poziomie.
Zrealizuj kryteria odbioru z karty i uruchom testy. Zacommituj własne zmiany. Podaj branch, SHA, polecenie weryfikacji, wynik i ewentualne ograniczenia.
```

### K03 · Klastry gęstościowe i szum

Można wysłać po scaleniu F04.

```text
Wykonaj K03 z karty zadania/K03.md w repozytorium Methadata. Pracuj w osobnym worktree na branchu codex/cluster-density, opartym na lokalnym main zawierającym F04.
DBSCAN z eps i min_samples, następnie HDBSCAN/OPTICS dla zmiennej gęstości. Pozwalają pozostawić część materiałów jako szum.
Zrealizuj kryteria odbioru z karty i uruchom testy. Zacommituj własne zmiany. Podaj branch, SHA, polecenie weryfikacji, wynik i ewentualne ograniczenia.
```

### K04 · Medoidy i centroidy

Można wysłać po scaleniu F04, E02.

```text
Wykonaj K04 z karty zadania/K04.md w repozytorium Methadata. Pracuj w osobnym worktree na branchu codex/cluster-medoids, opartym na lokalnym main zawierającym F04, E02.
k-medoids na odległościach wybiera faktyczny materiał jako reprezentanta. Na jednolitych wektorach porównać z k-means po normalizacji.
Zrealizuj kryteria odbioru z karty i uruchom testy. Zacommituj własne zmiany. Podaj branch, SHA, polecenie weryfikacji, wynik i ewentualne ograniczenia.
```

### K05 · Społeczności Leiden

Można wysłać po scaleniu F04.

```text
Wykonaj K05 z karty zadania/K05.md w repozytorium Methadata. Pracuj w osobnym worktree na branchu codex/cluster-leiden, opartym na lokalnym main zawierającym F04.
Ważony graf podobieństw, obniżenie wagi popularnych śladów i podział Leiden. Parametr rozdzielczości steruje wielkością grup.
Zrealizuj kryteria odbioru z karty i uruchom testy. Zacommituj własne zmiany. Podaj branch, SHA, polecenie weryfikacji, wynik i ewentualne ograniczenia.
```

### K07 · Nakładające się grupy

Można wysłać po scaleniu F04.

```text
Wykonaj K07 z karty zadania/K07.md w repozytorium Methadata. Pracuj w osobnym worktree na branchu codex/cluster-overlapping, opartym na lokalnym main zawierającym F04.
Perkolacja klik lub klastrowanie krawędzi umożliwiają wielokrotną przynależność. Materiał może powielać cudzy szablon i jednocześnie należeć do rodziny narracyjnej.
Zrealizuj kryteria odbioru z karty i uruchom testy. Zacommituj własne zmiany. Podaj branch, SHA, polecenie weryfikacji, wynik i ewentualne ograniczenia.
```

### K10 · Similarity Network Fusion

Można wysłać po scaleniu F06.

```text
Wykonaj K10 z karty zadania/K10.md w repozytorium Methadata. Pracuj w osobnym worktree na branchu codex/cluster-network-fusion, opartym na lokalnym main zawierającym F06.
Osobne sieci podobieństwa dla tekstu, obrazu i struktury, następnie Similarity Network Fusion i grupowanie sieci wynikowej.
Zrealizuj kryteria odbioru z karty i uruchom testy. Zacommituj własne zmiany. Podaj branch, SHA, polecenie weryfikacji, wynik i ewentualne ograniczenia.
```

### K13 · Miękkie przypisania

Można wysłać po scaleniu F04, E02.

```text
Wykonaj K13 z karty zadania/K13.md w repozytorium Methadata. Pracuj w osobnym worktree na branchu codex/cluster-soft-membership, opartym na lokalnym main zawierającym F04, E02.
Miękkie przypisania przez fuzzy c-means lub mieszaninę Gaussów na zredukowanych wektorach. Nadaje się do materiałów granicznych.
Zrealizuj kryteria odbioru z karty i uruchom testy. Zacommituj własne zmiany. Podaj branch, SHA, polecenie weryfikacji, wynik i ewentualne ograniczenia.
```

### K14 · Przyrostowe dołączanie reklamy

Można wysłać po scaleniu F06.

```text
Wykonaj K14 z karty zadania/K14.md w repozytorium Methadata. Pracuj w osobnym worktree na branchu codex/cluster-incremental, opartym na lokalnym main zawierającym F06.
Wariant przyrostowy porównuje nowy materiał z reprezentantem i kilkoma członkami klastra. Przy słabym pokryciu zwraca brak przypisania.
Zrealizuj kryteria odbioru z karty i uruchom testy. Zacommituj własne zmiany. Podaj branch, SHA, polecenie weryfikacji, wynik i ewentualne ograniczenia.
```

### S01 · Dane o płatniku i emisji

Można wysłać po scaleniu F01.

```text
Wykonaj S01 z karty zadania/S01.md w repozytorium Methadata. Pracuj w osobnym worktree na branchu codex/source-payer, opartym na lokalnym main zawierającym F01.
Z publicznego rekordu przejrzystości reklam odczytać deklarowanego płatnika, podmiot promowany, ID emisji i okres wyświetlania, o ile źródło je ujawnia.
Zrealizuj kryteria odbioru z karty i uruchom testy. Zacommituj własne zmiany. Podaj branch, SHA, polecenie weryfikacji, wynik i ewentualne ograniczenia.
```

### S04 · Deklarowany odbiorca płatności

Można wysłać po scaleniu X01.

```text
Wykonaj S04 z karty zadania/S04.md w repozytorium Methadata. Pracuj w osobnym worktree na branchu codex/source-payee, opartym na lokalnym main zawierającym X01.
Z dostarczonej instrukcji płatniczej lub widocznej oferty odczytać wskazany rachunek odbiorcy, adres portfela z siecią lub publiczny identyfikator sprzedawcy u operatora płatności. Porównywać w obrębie danego systemu, w UI maskować wartość.
Zrealizuj kryteria odbioru z karty i uruchom testy. Zacommituj własne zmiany. Podaj branch, SHA, polecenie weryfikacji, wynik i ewentualne ograniczenia.
```

### S05 · Kody polecające i afiliacja

Można wysłać po scaleniu X01.

```text
Wykonaj S05 z karty zadania/S05.md w repozytorium Methadata. Pracuj w osobnym worktree na branchu codex/source-affiliate, opartym na lokalnym main zawierającym X01.
Z oznaczonych linków partnerskich, banerów i opisów twórców wydobyć identyfikator partnera lub kod polecający wraz z nazwą programu.
Zrealizuj kryteria odbioru z karty i uruchom testy. Zacommituj własne zmiany. Podaj branch, SHA, polecenie weryfikacji, wynik i ewentualne ograniczenia.
```

### S06 · Docelowa aplikacja i powiązanie domeny

Można wysłać po scaleniu F01.

```text
Wykonaj S06 z karty zadania/S06.md w repozytorium Methadata. Pracuj w osobnym worktree na branchu codex/source-mobile-app, opartym na lokalnym main zawierającym F01.
Z linku do sklepu odczytać ID aplikacji i sklep. Dla Androida dołączyć publiczną deklarację powiązania domeny z pakietem i odciskiem certyfikatu, gdy występuje.
Zrealizuj kryteria odbioru z karty i uruchom testy. Zacommituj własne zmiany. Podaj branch, SHA, polecenie weryfikacji, wynik i ewentualne ograniczenia.
```

### S07 · Pochodzenie materiału według C2PA

Można wysłać po scaleniu F01.

```text
Wykonaj S07 z karty zadania/S07.md w repozytorium Methadata. Pracuj w osobnym worktree na branchu codex/source-media-provenance, opartym na lokalnym main zawierającym F01.
Z oryginału grafiki lub filmu odczytać manifest C2PA, wskazane materiały źródłowe, relacje między wersjami i wynik walidacji.
Zrealizuj kryteria odbioru z karty i uruchom testy. Zacommituj własne zmiany. Podaj branch, SHA, polecenie weryfikacji, wynik i ewentualne ograniczenia.
```

### S08 · Historia i wersjonowanie ofert

Można wysłać po scaleniu F02, R01.

```text
Wykonaj S08 z karty zadania/S08.md w repozytorium Methadata. Pracuj w osobnym worktree na branchu codex/source-history, opartym na lokalnym main zawierającym F02, R01.
Z kolejnych zgłoszeń i dostępnych publicznych kopii archiwalnych pobierać wersje tej samej strony wraz z czasem i źródłem. Liczyć różnice oferty, kontaktu i celu CTA.
Zrealizuj kryteria odbioru z karty i uruchom testy. Zacommituj własne zmiany. Podaj branch, SHA, polecenie weryfikacji, wynik i ewentualne ograniczenia.
```

### S09 · Sprzedawcy i dystrybucja reklam

Można wysłać po scaleniu F01.

```text
Wykonaj S09 z karty zadania/S09.md w repozytorium Methadata. Pracuj w osobnym worktree na branchu codex/source-distribution, opartym na lokalnym main zawierającym F01.
Z publicznych ads.txt i sellers.json pozyskać pary system reklamowy–konto sprzedawcy. Jeśli wydawca dostarcza dane konkretnej emisji, dołączyć zapisany łańcuch sprzedaży.
Zrealizuj kryteria odbioru z karty i uruchom testy. Zacommituj własne zmiany. Podaj branch, SHA, polecenie weryfikacji, wynik i ewentualne ograniczenia.
```

### S10 · Produkt, oferta i sprzedawca

Można wysłać po scaleniu X01.

```text
Wykonaj S10 z karty zadania/S10.md w repozytorium Methadata. Pracuj w osobnym worktree na branchu codex/source-offer-id, opartym na lokalnym main zawierającym X01.
Z opakowania, karty produktu lub marketplace odczytać GTIN, ID oferty i ID sprzedawcy, jeśli są dostępne. Lokalny SKU zapisać wraz z wystawcą.
Zrealizuj kryteria odbioru z karty i uruchom testy. Zacommituj własne zmiany. Podaj branch, SHA, polecenie weryfikacji, wynik i ewentualne ograniczenia.
```

### X02 · Statyczne cechy JS, CSS, SVG i fontów

Można wysłać po scaleniu X01.

```text
Wykonaj X02 z karty zadania/X02.md w repozytorium Methadata. Pracuj w osobnym worktree na branchu codex/extract-code, opartym na lokalnym main zawierającym X01.
Z kodu zewnętrznego i inline wyprowadź importy, wybrane literały, nazwy zdarzeń, wzorce składniowe i sygnatury po normalizacji. Z CSS/SVG wydobądź układ reguł, fonty, odnośniki i zależności.
Zrealizuj kryteria odbioru z karty i uruchom testy. Zacommituj własne zmiany. Podaj branch, SHA, polecenie weryfikacji, wynik i ewentualne ograniczenia.
```

### X03 · HAR, przekierowania i infrastruktura

Można wysłać po scaleniu F01.

```text
Wykonaj X03 z karty zadania/X03.md w repozytorium Methadata. Pracuj w osobnym worktree na branchu codex/extract-network, opartym na lokalnym main zawierającym F01.
Parsuj żądania, odpowiedzi i ich treści, jawne przekierowania, inicjatory, typy i czasy. Z załączonych zapisów DNS/TLS wydobądź host, IP i pola certyfikatu. Treści z HAR przekaż jako artefakty pochodne.
Zrealizuj kryteria odbioru z karty i uruchom testy. Zacommituj własne zmiany. Podaj branch, SHA, polecenie weryfikacji, wynik i ewentualne ograniczenia.
```

### X04 · Dokumenty, metadane i stan przeglądarki

Można wysłać po scaleniu F01.

```text
Wykonaj X04 z karty zadania/X04.md w repozytorium Methadata. Pracuj w osobnym worktree na branchu codex/extract-documents, opartym na lokalnym main zawierającym F01.
Obsłuż PDF, JSON i TXT, informacje o czasie i tytule pobrania, manifesty sum oraz zapisane atrybuty cookies i nazwy kluczy Web Storage.
Zrealizuj kryteria odbioru z karty i uruchom testy. Zacommituj własne zmiany. Podaj branch, SHA, polecenie weryfikacji, wynik i ewentualne ograniczenia.
```

### X05 · Obrazy, klatki i ścieżki multimedialne

Można wysłać po scaleniu F01.

```text
Wykonaj X05 z karty zadania/X05.md w repozytorium Methadata. Pracuj w osobnym worktree na branchu codex/extract-media, opartym na lokalnym main zawierającym F01.
Wykryj format i rolę zasobu, wymiary i metadane. Dla dostarczonych nagrań udostępnij klatki z czasem i referencje do ścieżek audio.
Zrealizuj kryteria odbioru z karty i uruchom testy. Zacommituj własne zmiany. Podaj branch, SHA, polecenie weryfikacji, wynik i ewentualne ograniczenia.
```

## Eksperymenty 2 · Równolegle

### E04 · Podobieństwo URL i przepływów sieciowych

Można wysłać po scaleniu X03.

```text
Wykonaj E04 z karty zadania/E04.md w repozytorium Methadata. Pracuj w osobnym worktree na branchu codex/exp-url-har, opartym na lokalnym main zawierającym X03.
Rozłożyć URL na host, wzorzec ścieżki i parametry. Z HAR wydobyć udokumentowane przekierowania HTTP oraz identyfikatory integracji z kontekstem dostawcy. Osobno liczyć zgodność kluczy, wartości i kolejności hostów.
Zrealizuj kryteria odbioru z karty i uruchom testy. Zacommituj własne zmiany. Podaj branch, SHA, polecenie weryfikacji, wynik i ewentualne ograniczenia.
```

### E09 · Podobieństwo grafik po przeskalowaniu i kadrowaniu

Można wysłać po scaleniu X05.

```text
Wykonaj E09 z karty zadania/E09.md w repozytorium Methadata. Pracuj w osobnym worktree na branchu codex/exp-perceptual, opartym na lokalnym main zawierającym X05.
Hash percepcyjny pHash i odległość Hamminga dla ilustracji i wycinków. Porównać również lokalne punkty charakterystyczne z weryfikacją geometryczną dla kadrowania.
Zrealizuj kryteria odbioru z karty i uruchom testy. Zacommituj własne zmiany. Podaj branch, SHA, polecenie weryfikacji, wynik i ewentualne ograniczenia.
```

### E10 · OCR i układ reklamy

Można wysłać po scaleniu X05, X01.

```text
Wykonaj E10 z karty zadania/E10.md w repozytorium Methadata. Pracuj w osobnym worktree na branchu codex/exp-ocr-layout, opartym na lokalnym main zawierającym X05, X01.
OCR z prostokątami → odszukanie podobnych zdań i układu tytuł–ilustracja–przycisk na zrzucie. Koordynaty względne ułatwiają porównanie rozdzielczości.
Zrealizuj kryteria odbioru z karty i uruchom testy. Zacommituj własne zmiany. Podaj branch, SHA, polecenie weryfikacji, wynik i ewentualne ograniczenia.
```

### E11 · Embeddingi wizualne i multimodalne

Można wysłać po scaleniu X05.

```text
Wykonaj E11 z karty zadania/E11.md w repozytorium Methadata. Pracuj w osobnym worktree na branchu codex/exp-visual-embedding, opartym na lokalnym main zawierającym X05.
Wektory wizyjne dla ilustracji głównej i jej fragmentów. Porównać cosinusy, zachować dopasowany wycinek. Osobny wariant porównuje podpis tekstowy i obraz w modelu multimodalnym.
Zrealizuj kryteria odbioru z karty i uruchom testy. Zacommituj własne zmiany. Podaj branch, SHA, polecenie weryfikacji, wynik i ewentualne ograniczenia.
```

### E12 · Wspólny zestaw kodu i komponentów strony

Można wysłać po scaleniu X01, X02.

```text
Wykonaj E12 z karty zadania/E12.md w repozytorium Methadata. Pracuj w osobnym worktree na branchu codex/exp-production-kit, opartym na lokalnym main zawierającym X01, X02.
Fingerprint arkuszy CSS, rzadkich nazw klas, lokalnych skryptów i budowy formularza. Wariant mierzy współwystępowanie kilku rzadkich elementów zestawu.
Zrealizuj kryteria odbioru z karty i uruchom testy. Zacommituj własne zmiany. Podaj branch, SHA, polecenie weryfikacji, wynik i ewentualne ograniczenia.
```

### E13 · Sekwencja działań na stronie

Można wysłać po scaleniu X01, X03.

```text
Wykonaj E13 z karty zadania/E13.md w repozytorium Methadata. Pracuj w osobnym worktree na branchu codex/exp-funnel, opartym na lokalnym main zawierającym X01, X03.
Z HTML i HAR odtworzyć obserwowalne etapy, np. artykuł → wezwanie → formularz. Porównać kolejność etapów odległością edycyjną lub najdłuższym wspólnym podciągiem.
Zrealizuj kryteria odbioru z karty i uruchom testy. Zacommituj własne zmiany. Podaj branch, SHA, polecenie weryfikacji, wynik i ewentualne ograniczenia.
```

### E15 · Zbieżność emisji w czasie

Można wysłać po scaleniu S01.

```text
Wykonaj E15 z karty zadania/E15.md w repozytorium Methadata. Pracuj w osobnym worktree na branchu codex/exp-temporal, opartym na lokalnym main zawierającym S01.
Po uzyskaniu wiarygodnych dat emisji wykrywać serie pojawień jednego wzorca na kolejnych domenach i porównać z modelem bez czasu.
Zrealizuj kryteria odbioru z karty i uruchom testy. Zacommituj własne zmiany. Podaj branch, SHA, polecenie weryfikacji, wynik i ewentualne ograniczenia.
```

### E16 · Podobieństwo audio i wideo

Można wysłać po scaleniu X05.

```text
Wykonaj E16 z karty zadania/E16.md w repozytorium Methadata. Pracuj w osobnym worktree na branchu codex/exp-video-audio, opartym na lokalnym main zawierającym X05.
Dla nowych reklam wideo porównać klatki kluczowe, sekwencje ujęć, ścieżki audio i transkrypcje. Dopasowanie sekwencyjne może odnaleźć krótki fragment dłuższej kreacji.
Zrealizuj kryteria odbioru z karty i uruchom testy. Zacommituj własne zmiany. Podaj branch, SHA, polecenie weryfikacji, wynik i ewentualne ograniczenia.
```

### K06 · Grupowanie spektralne

Można wysłać po scaleniu K05.

```text
Wykonaj K06 z karty zadania/K06.md w repozytorium Methadata. Pracuj w osobnym worktree na branchu codex/cluster-spectral, opartym na lokalnym main zawierającym K05.
Symetryczny graf wzajemnych najbliższych sąsiadów, następnie klastrowanie spektralne. Może odzyskać grupy o nieregularnym kształcie.
Zrealizuj kryteria odbioru z karty i uruchom testy. Zacommituj własne zmiany. Podaj branch, SHA, polecenie weryfikacji, wynik i ewentualne ograniczenia.
```

### K08 · Biklastrowanie materiałów i cech

Można wysłać po scaleniu E14, F04.

```text
Wykonaj K08 z karty zadania/K08.md w repozytorium Methadata. Pracuj w osobnym worktree na branchu codex/cluster-bicluster, opartym na lokalnym main zawierającym E14, F04.
Na macierzy materiał × ślad wyszukiwać podmacierze współwystępowania lub często powtarzające się zbiory śladów. Grupa wychodzi razem z cechami, które ją definiują.
Zrealizuj kryteria odbioru z karty i uruchom testy. Zacommituj własne zmiany. Podaj branch, SHA, polecenie weryfikacji, wynik i ewentualne ograniczenia.
```

### K11 · Konsensus klastrów

Można wysłać po scaleniu F06, K02.

```text
Wykonaj K11 z karty zadania/K11.md w repozytorium Methadata. Pracuj w osobnym worktree na branchu codex/cluster-consensus, opartym na lokalnym main zawierającym F06, K02.
Wielokrotnie klastrować przy różnych rodzinach cech i parametrach. Macierz zgodności liczy odsetek wspólnych przypisań, a próg wyłania stabilne rdzenie.
Zrealizuj kryteria odbioru z karty i uruchom testy. Zacommituj własne zmiany. Podaj branch, SHA, polecenie weryfikacji, wynik i ewentualne ograniczenia.
```

### K12 · Klastrowanie z ograniczeniami analityka

Można wysłać po scaleniu F03, K02.

```text
Wykonaj K12 z karty zadania/K12.md w repozytorium Methadata. Pracuj w osobnym worktree na branchu codex/cluster-constrained, opartym na lokalnym main zawierającym F03, K02.
Analityk oznacza must-link/cannot-link dla konkretnego rodzaju relacji. Aglomeracja z ograniczeniami lub correlation clustering uwzględnia potwierdzenia i sprzeczności.
Zrealizuj kryteria odbioru z karty i uruchom testy. Zacommituj własne zmiany. Podaj branch, SHA, polecenie weryfikacji, wynik i ewentualne ograniczenia.
```

### K15 · Spacery losowe i Infomap

Można wysłać po scaleniu E14, F04.

```text
Wykonaj K15 z karty zadania/K15.md w repozytorium Methadata. Pracuj w osobnym worktree na branchu codex/cluster-graph-walk, opartym na lokalnym main zawierającym E14, F04.
Na grafie materiał–ślad użyć spacerów losowych z restartem do rankingu sąsiadów, a Infomap do wyszukiwania społeczności.
Zrealizuj kryteria odbioru z karty i uruchom testy. Zacommituj własne zmiany. Podaj branch, SHA, polecenie weryfikacji, wynik i ewentualne ograniczenia.
```

### R04 · Opcjonalne cykliczne przyjmowanie nowych emisji

Można wysłać po scaleniu S01, R01, F03.

```text
Wykonaj R04 z karty zadania/R04.md w repozytorium Methadata. Pracuj w osobnym worktree na branchu codex/source-monitor, opartym na lokalnym main zawierającym S01, R01, F03.
Dla podłączonej biblioteki przygotuj okresowy import z kursorem, deduplikacją i wywołaniem analizy dla nowych lub zmienionych rekordów.
Zrealizuj kryteria odbioru z karty i uruchom testy. Zacommituj własne zmiany. Podaj branch, SHA, polecenie weryfikacji, wynik i ewentualne ograniczenia.
```

### S02 · Dekodowanie kodów QR

Można wysłać po scaleniu X05.

```text
Wykonaj S02 z karty zadania/S02.md w repozytorium Methadata. Pracuj w osobnym worktree na branchu codex/source-qr, opartym na lokalnym main zawierającym X05.
Dekodować kody QR ze zdjęć, zrzutów i klatek filmu. Zapisać typ i znormalizowany cel, prostokąt oraz czas klatki.
Zrealizuj kryteria odbioru z karty i uruchom testy. Zacommituj własne zmiany. Podaj branch, SHA, polecenie weryfikacji, wynik i ewentualne ograniczenia.
```

## Eksperymenty 3 · Jedna wiadomość

### S03 · Kontakt handlowy i konto komunikatora

Można wysłać po scaleniu X01, E10.

```text
Wykonaj S03 z karty zadania/S03.md w repozytorium Methadata. Pracuj w osobnym worktree na branchu codex/source-contact, opartym na lokalnym main zawierającym X01, E10.
Z ogłoszenia, stopki lub CTA odczytać publiczny telefon handlowy, e-mail kontaktowy i identyfikator konta komunikatora wraz z rolą.
Zrealizuj kryteria odbioru z karty i uruchom testy. Zacommituj własne zmiany. Podaj branch, SHA, polecenie weryfikacji, wynik i ewentualne ograniczenia.
```
