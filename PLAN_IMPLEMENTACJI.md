# Kolekcjoner Fejków · plan implementacji i przydział zadań

**Zacznij od F01.** Jednemu modelowi przekaż kartę F01 i repozytorium. Po scaleniu F01 rozdziel W01 (kontrakt HTTP), F02 (import), F03 (runner) i X01 (HTML). Po W01 frontend może ruszyć na przykładach odpowiedzi. Backend W02 podłączy realną analizę po F04 i R01. Wszystkie karty są podlinkowane na końcu.

**Polecenie do skopiowania** (podmień ID):

> Wykonaj zadanie z pliku zadania/F01.md w tym repozytorium. Karta określa branch, zależności, zakres oraz test odbiorczy. Zaimplementuj je, uruchom testy i oddaj zmiany na branchu wraz z poleceniem odtworzenia wyniku. W opisie podaj, co sprawdziłeś na rzeczywistych danych, a co na próbce kontrolnej.

W repozytorium wyjściowym Git śledził tylko README. **Przed rozdzieleniem pracy** udostępnij modelom tę samą rewizję z planami, kartami i [wymaganiami](WYMAGANIA_PROJEKTU.md). Modele pracujące w niezależnych checkoutach potrzebują osobnego dostępu do korpusu, aby odtworzyć pomiary. Do implementacji i CI wystarczą małe, jawnie opisane próbki F01. Kopiowanie surowego archiwum, HAR ani odszyfrowanej listy do historii Git nie jest potrzebne. [Katalog metod i analiza korpusu](PLAN_KLASTROWANIA.md)

## Decyzje startowe

Projekt ma dwa osobno uruchamiane komponenty. **Frontend** w `frontend/` to React, TypeScript i Vite. **Backend** w `backend/` to FastAPI udostępniające wersjonowane HTTP API. Backend i CLI importują ten sam silnik Python z `src/methadata/`. Silnik zapisuje rekordy JSONL według wersjonowanego JSON Schema oraz pliki adresowane po SHA-256. Python używa pytest. Zależności OCR, embeddingów i eksperymentów grafowych są opcjonalne.

```text
Przeglądarka → React/Vite → /api/v1 → FastAPI → silnik Python → wyniki i artefakty
CLI eksperymentów ───────────────────────────→ ten sam silnik
```

**Docelowy podgląd po W03.** Z katalogu głównego uruchamiasz `docker compose up --build`, otwierasz `http://localhost:5173` i wybierasz „Uruchom przykład”. Do pierwszego podglądu wystarcza Docker z Compose i mały zestaw demonstracyjny dostarczony z projektem. Przeglądarka odczytuje wynik i dowody przez API. Backend zapisuje przebiegi w wolumenie. Opcjonalny korpus hackathonowy jest używany w scenariuszu R02.

**Granica odpowiedzialności.** W01 ustala kontrakt OpenAPI przed równoległą implementacją klienta i serwera. F05 obsługuje zgłoszenie, stan przebiegu i wyświetlanie dowodów. W02 odpowiada za walidację, przyjęcie pliku lub URL, uruchomienie metody i dostęp do wyników po ID. W03 łączy procesy przez proxy tego samego origin. W04 sprawdza tę drogę w przeglądarce. Przykłady kontraktowe wspierają testy UI, a domyślny podgląd liczy wynik na serwerze.

| Etap | Właściciel wyniku | Umowa między modułami |
|---|---|---|
| Pozyskanie i utrwalenie | F02, R01, adaptery S | Zewnętrzne wejście → Observation, Artifact i historia wersji. Ten sam plik może wystąpić w wielu obserwacjach. Kolejne wzbogacenie zachowuje datę i źródło. |
| Ekstrakcja | X i S, część E | Oryginał lub artefakt pochodny → Feature, ExtractionStatus i referencje do źródła. Brak danych, pusty wynik i błąd są rozróżnialne. |
| Porównanie | E, następnie K09 | Para kanonicznych obserwacji → PairEvidence. Każda relacja ma typ, wynik lub null i identyfikowalny dowód. |
| Grupowanie | K, F04, F06 | Zestaw obserwacji i zamrożone wejścia → ClusterRun z przypisaniami i krawędziami. Algorytm może korzystać także z udokumentowanej macierzy cech lub grafu materiał–ślad. |
| Pokaz i odbiór | F05, W02–W04, R02, R03 | Nowy materiał → analiza → grupa lub uzasadniony brak przypisania → podgląd śladu dla konkretnej pary. |

F01 utrzymuje wspólne typy, format referencji i sposób odkrywania adapterów. F03 utrzymuje runner, F05 interfejs przeglądarkowy, W02 backend HTTP, a R02 konfigurację dla jury. Pozostali właściciele dodają moduł, testy, lokalną konfigurację i opcjonalne zależności w katalogu swojego zadania. F01 definiuje ten mechanizm także dla nazw z myślnikiem. R02 scala wymagane zależności do wspólnej instalacji. Branch eksperymentalny może działać przez runner od razu po scaleniu.

## Kolejność wydawania

| Kamień milowy | Co przydzielić | Kiedy uznać etap za zakończony |
|---|---|---|
| 1. Wspólny start | F01, potem W01, F02, F03 i X01. Po W01 rusza F05. Po F01 mogą też ruszyć E01 i K01 na małych próbkach. | Dwa niezależne moduły wymieniają rekordy według tego samego schematu. Frontend odczytuje przykładową odpowiedź zgodną z OpenAPI. |
| 2. Pierwszy przebieg od wejścia do grupy | F04 integruje E01 i K01 z importerem i runnerem. Równolegle X02–X05 oraz R01 rozwijają ekstrakcję i rzeczywiste wejścia. | Na 14 unikalnych stronach powstaje 91 porównań. Próg 0,5 odtwarza dwie rodziny zasobów. CLI uruchamia analizę nowego pliku. |
| 2a. Podgląd webowy | W02 dostarcza API po F04 i R01. W03 łączy je z F05, a W04 uruchamia test przeglądarkowy. | Jedno polecenie uruchamia obie usługi. Kliknięcie przykładu kończy się widocznym dowodem, również bez prywatnego zbioru. |
| 3. Porównanie hipotez | E02, E03 i E04, następnie K02. K09 łączy niezależne rodziny dowodów, F06 sprawdza ich rzeczywisty wkład. | Ten sam runner pokazuje zmianę wyników po włączeniu tekstu, DOM lub zasobów. Widać, kiedy pojedynczy wspólny element błędnie skleja grupy. |
| 4. Wersja dla jury | Po W04 i F06 karta R02 udostępnia profil z korpusem przez działający frontend i backend. E18 i R03 dodają interaktywne wyłączanie dowodów. | Dodanie p15 do indeksu z p2 i p7 zwraca rodzinę z dwustronnym dowodem. Sam URL może zakończyć się wynikiem „za mało danych”. Demo można powtórzyć od czystej instalacji. |
| 5. Dalsze eksperymenty | Przydzielać pozostałe E i K według dostępnych danych. S01–S10 można rozwijać równolegle po spełnieniu zależności z kart. Pierwszy pokaz terenowy łączy S02 i S03. | Każdy merge ma wynik na wspólnym zbiorze lub jawnie wydzielonej próbce terenowej, porównanie i przykład zawodności. |

**Pierwszy przydział dla czterech modeli po F01.** Pierwszy bierze W01 → F05, drugi F02 → R01, trzeci F03 → K01, czwarty E01 → F04 po scaleniu zależności. Następnie W02 → W03 → W04 daje sprawdzony podgląd. Równolegle wolne modele mogą wziąć X01 → E02/E03 i rozwinąć fuzję dla jury.

## Ocena i scalanie

1. Każdy model zaczyna na rewizji zawierającej scalone zmiany z zadań wymienionych w polu „Wymaga”. Do pracy równoległej używaj oddzielnych checkoutów lub worktree. Każda karta ma własny branch. F04 odpowiada za integrację E01/K01, a F06 za odbiór K09 na korpusie. Algorytmy powstają po jednym razie.
2. F03 zamraża manifest, konfigurację, seed i podział danych. Opiekunowie oznaczają relacje na trzech osiach (szablon, narracja, kandydat na kampanię), zapisując tak / nie / nie wiadomo. Kopie pozostają w tej samej części zbioru. Etykiety ręczne są wejściem do oceny. Przy ich braku raport pokazuje pokrycie i sprawdzalne podobieństwa.
3. Dla eksperymentu E utrzymuj stały algorytm grupowania. Dla K utrzymuj stałe dane i reprezentację, podaj obsługę brakujących porównań. Raportuj TP, FP, FN, precision, recall, F1, pokrycie, koszt i przypadek błędny zgodnie z §6 katalogu metod. Ocenę na sztucznych wariantach przedstaw oddzielnie. Na materiałach o nieznanej przynależności do kampanii wstrzymaj ocenę trafności kampanii.
4. W opisie zmiany podaj komendę, wersję danych, rzeczywisty wynik i ograniczenia. R02 włącza metodę do demo po sprawdzeniu wpływu na jakość, przy remisie wybiera prostszą do wyjaśnienia. Wynik ujemny też kończy eksperyment i zostawia metodę dostępną poza konfiguracją demo.
5. Przy odbiorze aplikacji wykonaj trzy przebiegi przez UI: p15 jako nowy materiał, URL o skąpych danych i nowe zgłoszenie spoza schematu nazw pN. Sprawdź odnośniki do dowodów, komunikat o brakujących danych oraz powtarzalność po restarcie. Próbę QR + kontakt dodaj po S02/S03. Test kolekcji online i test powtarzalny na zapisanej odpowiedzi raportuj osobno.

## Kolejka zadań

Każdy link otwiera osobny prompt. Kolumna „Wymaga” podaje zależności do wykonania całej karty. Dodatkowe warunki dotyczące danych są podane w samym zadaniu.

### Fundament i integracja metod

| Karta | Wymaga |
|---|---|
| [F01 · Kontrakt, próbki i szkielet projektu](zadania/F01.md) | Można rozpocząć od razu |
| [F02 · Importer, pełny inwentarz i deduplikacja](zadania/F02.md) | F01 |
| [F03 · Runner eksperymentów i ocena par](zadania/F03.md) | F01 |
| [F04 · Integracja i odtworzenie bazowego pomiaru](zadania/F04.md) | F02, F03, E01, K01 |
| [F05 · Frontend React i widok dowodów](zadania/F05.md) | W01 |
| [F06 · Ocena fuzji na rzeczywistym korpusie](zadania/F06.md) | F04, K09, E02, E03 |

### API i podgląd aplikacji

| Karta | Wymaga |
|---|---|
| [W01 · Kontrakt HTTP dla backendu i frontendu](zadania/W01.md) | F01 |
| [W02 · Backend FastAPI i uruchamianie analiz](zadania/W02.md) | W01, F04, R01 |
| [W03 · Start obu usług jednym poleceniem](zadania/W03.md) | W02, F05 |
| [W04 · Test przeglądarkowy pełnego podglądu](zadania/W04.md) | W03 |

### Pełna ekstrakcja artefaktów

| Karta | Wymaga |
|---|---|
| [X01 · HTML, DOM, formularze i zawartość MHTML](zadania/X01.md) | F01 |
| [X02 · Statyczne cechy JS, CSS, SVG i fontów](zadania/X02.md) | X01 |
| [X03 · HAR, przekierowania i infrastruktura](zadania/X03.md) | F01 |
| [X04 · Dokumenty, metadane i stan przeglądarki](zadania/X04.md) | F01 |
| [X05 · Obrazy, klatki i ścieżki multimedialne](zadania/X05.md) | F01 |

### Eksperymenty podobieństwa

| Karta | Wymaga |
|---|---|
| [E01 · Identyczne zasoby i ważony Jaccard](zadania/E01.md) | F01 |
| [E02 · Podobieństwo tekstu TF-IDF](zadania/E02.md) | X01 |
| [E03 · Podobieństwo struktury DOM](zadania/E03.md) | X01 |
| [E04 · Podobieństwo URL i przepływów sieciowych](zadania/E04.md) | X03 |
| [E05 · Bliskie kopie tekstu i wyszukiwanie MinHash](zadania/E05.md) | X01 |
| [E06 · Podobieństwo semantyczne akapitów](zadania/E06.md) | X01 |
| [E07 · Schemat narracji i role bohaterów](zadania/E07.md) | X01 |
| [E08 · Osoby, marki i produkty](zadania/E08.md) | X01 |
| [E09 · Podobieństwo grafik po przeskalowaniu i kadrowaniu](zadania/E09.md) | X05 |
| [E10 · OCR i układ reklamy](zadania/E10.md) | X05, X01 |
| [E11 · Embeddingi wizualne i multimodalne](zadania/E11.md) | X05 |
| [E12 · Wspólny zestaw kodu i komponentów strony](zadania/E12.md) | X01, X02 |
| [E13 · Sekwencja działań na stronie](zadania/E13.md) | X01, X03 |
| [E14 · Rzadkie kombinacje śladów](zadania/E14.md) | E01, E02, E03 |
| [E15 · Zbieżność emisji w czasie](zadania/E15.md) | S01 |
| [E16 · Podobieństwo audio i wideo](zadania/E16.md) | X05 |
| [E17 · Uczenie podobieństwa na ocenionych parach](zadania/E17.md) | F03, F06 |
| [E18 · Co podtrzymuje połączenie](zadania/E18.md) | F06 |

### Algorytmy grupowania

| Karta | Wymaga |
|---|---|
| [K01 · Graf progowy i składowe spójne](zadania/K01.md) | F01 |
| [K02 · Aglomeracja average-link i complete-link](zadania/K02.md) | F04 |
| [K03 · Klastry gęstościowe i szum](zadania/K03.md) | F04 |
| [K04 · Medoidy i centroidy](zadania/K04.md) | F04, E02 |
| [K05 · Społeczności Leiden](zadania/K05.md) | F04 |
| [K06 · Grupowanie spektralne](zadania/K06.md) | K05 |
| [K07 · Nakładające się grupy](zadania/K07.md) | F04 |
| [K08 · Biklastrowanie materiałów i cech](zadania/K08.md) | E14, F04 |
| [K09 · Fuzja niezależnych rodzin dowodów](zadania/K09.md) | F04, E02 |
| [K10 · Similarity Network Fusion](zadania/K10.md) | F06 |
| [K11 · Konsensus klastrów](zadania/K11.md) | F06, K02 |
| [K12 · Klastrowanie z ograniczeniami analityka](zadania/K12.md) | F03, K02 |
| [K13 · Miękkie przypisania](zadania/K13.md) | F04, E02 |
| [K14 · Przyrostowe dołączanie reklamy](zadania/K14.md) | F06 |
| [K15 · Spacery losowe i Infomap](zadania/K15.md) | E14, F04 |

### Źródła i ślady terenowe

| Karta | Wymaga |
|---|---|
| [S01 · Dane o płatniku i emisji](zadania/S01.md) | F01 |
| [S02 · Dekodowanie kodów QR](zadania/S02.md) | X05 |
| [S03 · Kontakt handlowy i konto komunikatora](zadania/S03.md) | X01, E10 |
| [S04 · Deklarowany odbiorca płatności](zadania/S04.md) | X01 |
| [S05 · Kody polecające i afiliacja](zadania/S05.md) | X01 |
| [S06 · Docelowa aplikacja i powiązanie domeny](zadania/S06.md) | F01 |
| [S07 · Pochodzenie materiału według C2PA](zadania/S07.md) | F01 |
| [S08 · Historia i wersjonowanie ofert](zadania/S08.md) | F02, R01 |
| [S09 · Sprzedawcy i dystrybucja reklam](zadania/S09.md) | F01 |
| [S10 · Produkt, oferta i sprzedawca](zadania/S10.md) | X01 |

### Zgłoszenia, integracja i wydanie

| Karta | Wymaga |
|---|---|
| [R01 · Przyjęcie rzeczywistego zgłoszenia i pozyskanie strony](zadania/R01.md) | F02 |
| [R02 · Integracja demonstratora dla jury](zadania/R02.md) | W04, F06 |
| [R03 · Interaktywny pokaz wyłączania dowodów](zadania/R03.md) | R02, E18 |
| [R04 · Opcjonalne cykliczne przyjmowanie nowych emisji](zadania/R04.md) | S01, R01, F03 |
