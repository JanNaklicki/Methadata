Methadata — wymagania dla silnika analitycznego i wizualizacji powiązań
Proponuję potraktować Wasz projekt dla NASK jako platformę analityczną typu Campaign Intelligence, a nie zwykły detektor scamów.

Najważniejsze będzie połączenie trzech elementów:
1. Extraction Engine — wyciąganie informacji z reklam, stron i metadanych.
2. Correlation Engine — wyliczanie podobieństw i identyfikowanie wspólnych śladów.
3. Investigation UI — prezentacja kampanii, relacji i dowodów w sposób zrozumiały dla analityka.
W zadaniu NASK szczególnie podkreślono analizę podobieństwa wizualnego, semantycznego i konceptualnego, możliwość wykorzystania metadanych oraz prezentację powiązań, np. jako graf. Estetyka samego demonstratora nie jest oceniana jakościowo — liczy się użyteczność mechanizmów analitycznych.

Poniżej przedstawiam proponowaną specyfikację produktu. Progi, wagi i metryki są naszymi propozycjami projektowymi, a nie narzuconymi wymaganiami NASK.
1. Co powinien umieć system?
Główny przepływ danych
INPUT

Screenshot • URL • tekst reklamy • plik • import zbiorczy


Tekst

OCR, NLP, encje


Obraz

pHash, CLIP


WWW

DNS, URL, HTML




FEATURE STORE

Ujednolicone cechy każdej reklamy + dane źródłowe


CORRELATION ENGINE

Candidate retrieval → scoring → filtrowanie relacji → clustering


INVESTIGATION UI

Kampanie • graf • evidence • timeline • wyszukiwarka


Wymagania funkcjonalne
ID	Wymaganie	Priorytet
F01	Import reklamy ze screenshota, pliku lub URL	P0
F02	Automatyczna ekstrakcja tekstu przez OCR	P0
F03	Normalizacja URL i ekstrakcja metadanych	P0
F04	Generowanie embeddingów tekstowych	P0
F05	Porównywanie podobieństwa reklam	P0
F06	Tworzenie grafu relacji i klastrów	P0
F07	Wyjaśnianie każdej wykrytej relacji	P0
F08	Porównywarka dwóch reklam	P0
F09	Embeddingi obrazów i pHash	P1
F10	Obsługa historii oraz osi czasu	P1
F11	Wyszukiwanie reklam po podobieństwie	P1
F12	Ręczne potwierdzanie lub odrzucanie relacji	P1
F13	Integracja z bibliotekami reklam i scheduler	P2
F14	Eksport raportów i grafów do JSON/CSV	P2

P0 — wymagane dla MVP. P1 — istotne rozszerzenie. P2 — opcjonalne na hackathonie.

2. Wymagania dotyczące obliczeń
Najważniejsza decyzja architektoniczna: nie traktować wszystkich cech reklam jednakowo.

Podobieństwo zdjęć nie oznacza, że reklamy należą do tej samej kampanii. Podobny slogan również nie jest dowodem. Natomiast wspólne identyfikatory śledzące, finalne adresy URL czy charakterystyczne zasoby stron mogą stanowić mocniejszą przesłankę.

Dlatego podzieliłbym obliczenia na pięć kategorii.
A. Text Similarity
Przetwarzamy tekst z reklamy i tekst wyodrębniony przez OCR.

Obliczamy:
- Podobieństwo semantyczne przez embeddingi.
- Podobieństwo dosłowne, np. TF-IDF / n-gramy.
- Wspólne frazy i słowa kluczowe.
- Występujące marki, produkty, osoby i organizacje.
- Podobieństwo narracji, np. obietnica wysokiego zwrotu z inwestycji.
Proponowany model: wielojęzyczny Sentence Transformer.

Główna metryka:
\[
S_{\text{text}}(A,B)=\frac{v_A\cdot v_B}{\|v_A\|\|v_B\|}
\]

Wynik kosinusowy można na potrzeby interfejsu przekształcić do przedziału 0–1. Nie należy interpretować go jako prawdopodobieństwa wspólnej kampanii.
B. Visual Similarity
Tutaj wykorzystałbym dwa niezależne algorytmy.
Algorytm	Co wykrywa?	Przykład
pHash	Zbliżone wersje tego samego obrazu	Reklama po zmianie rozmiaru i kompresji
CLIP / OpenCLIP	Podobieństwo wizualno-semantyczne	Różne reklamy o podobnych motywach

Dla pHash obliczamy odległość Hamminga. Im mniejsza odległość, tym bardziej zbliżone hashe.

Dla CLIP stosujemy podobieństwo kosinusowe embeddingów obrazów.

Rekomendacja: pHash traktujcie jako sygnał zbliżonej kreacji, a CLIP jako szerszy sygnał podobieństwa wizualnego. Nie łączcie ich bezpośrednio bez normalizacji.
C. Infrastructure Similarity
To potencjalnie najbardziej wartościowa część systemu.

Analizujemy:
Cecha	Przykładowy dowód	Siła
Finalny URL	Ten sam adres docelowy	Bardzo wysoka
Tracking ID	Wspólny identyfikator analityczny	Wysoka
Charakterystyczne zasoby	Identyczny nietypowy plik JS	Wysoka
Struktura strony	Bardzo podobny DOM	Średnia
Domeny	Wspólny registrable domain	Zależna od kontekstu
Adres IP	Ten sam serwer	Niska bez innych sygnałów
CDN / hosting	Wspólny dostawca	Bardzo niska

Dla URL należy rozróżnić pełny adres, domenę, parametry reklamowe i stronę końcową po przekierowaniach. Popularne identyfikatory i współdzielone zasoby nie mogą automatycznie powodować łączenia kampanii.
D. Entity Similarity
Ekstrahujemy jednostki występujące w reklamach:
- Nazwy produktów.
- Nazwy marek.
- Nazwy organizacji.
- Nazwiska występujące w treści.
- Numery telefonów lub adresy kontaktowe, jeżeli są legalnie dostępne i potrzebne do analizy.
- Adresy domenowe.
NASK wskazuje także możliwość wykorzystania referencyjnej bazy wizerunków osób. W MVP zacząłbym od porównywania tych samych lub podobnych wycinków obrazów, a rozpoznawanie tożsamości pozostawił jako osobny, kontrolowany moduł. Wizerunek tej samej osoby nie dowodzi wspólnego organizatora.
E. Temporal Similarity
Dla każdej reklamy zapisujemy czas pozyskania i ewentualnie daty emisji, jeżeli źródło je udostępnia.

Pozwala to badać:
- Czy reklamy pojawiały się w podobnym okresie.
- Czy domeny były zastępowane kolejnymi.
- Czy wzorzec kampanii ewoluował.
- Czy określone szablony występują cyklicznie.
Nie używałbym czasu jako silnego dowodu samodzielnego. To raczej wsparcie dla innych sygnałów.

3. Jak wyliczać ostateczne powiązanie?
Proponuję hybrydowy model scoringowy, który daje zarówno wynik liczbowy, jak i listę dowodów.
Scoring v1
Na początku możemy zastosować ważoną sumę sygnałów.
\[
S(A,B)=\frac{\sum_{i\in O}w_i s_i(A,B)}{\sum_{i\in O}w_i}
\]

Gdzie:
- \(s_i\) — znormalizowany wynik danego rodzaju podobieństwa (0–1).
- \(w_i\) — waga danego sygnału.
- \(O\) — zbiór dostępnych, poprawnie obliczonych sygnałów.
Przykładowe początkowe wagi:
Cecha	Waga
Infrastructure	35%
Text	25%
Visual	20%
Entities	15%
Temporal	5%

Uwaga: brak danej cechy nie oznacza wyniku 0. System powinien rozróżniać „nieobecna”, „niezaobserwowana” i „nie udało się przeanalizować”. Przy brakujących danych wynik można przeliczać tylko z dostępnych cech, ale trzeba pokazywać niższą kompletność dowodów.

Nie stosowałbym też bezwarunkowo jednej sumy: np. popularny slogan i podobny obraz nie powinny przeważyć nad sprzecznymi mocnymi dowodami infrastrukturalnymi.
Symulator — jak wagi wpływają na wynik?
Reklama A vs reklama B

Fikcyjne sygnały, wszystkie dostępne

78%
Ważony wynik


Infrastruktura

Waga 35 • sygnał 95%




Tekst

Waga 25 • sygnał 72%




Obraz

Waga 20 • sygnał 81%




Encje

Waga 15 • sygnał 60%




Czas

Waga 5 • sygnał 40%




Wagi normalizowane automatycznie.
Reset

Jak klasyfikować relacje?
Na potrzeby pierwszej wersji można przetestować progi:
Wynik	Sposób prezentacji
0.85–1.00	Silne podobieństwo
0.65–0.85	Powiązanie wymagające sprawdzenia
0.45–0.65	Słaba wskazówka
poniżej 0.45	Brak wystarczającego podobieństwa

Nie są to progi statystycznie potwierdzone — należy je ustalić na oznaczonym zbiorze danych.

Ważniejsze od samego wyniku jest rozróżnienie similarity score i evidence confidence.

Przykładowo wysoki wynik podobieństwa obrazu nie daje wysokiej pewności wspólnej kampanii. Natomiast identyczny, charakterystyczny tracking ID, wspólna domena docelowa i podobny layout mogą razem stanowić znacznie silniejszy argument.
Wymagania dla silnika
- Każda relacja musi mieć listę uzasadniających ją cech.
- Każda cecha musi mieć zapisane źródło i wersję algorytmu.
- Wyniki muszą być odtwarzalne dla tych samych danych i konfiguracji.
- Analityk musi mieć możliwość odrzucenia fałszywej relacji.
- System musi obsługiwać brakujące dane.
- Powinna istnieć możliwość przeliczenia wyników po zmianie wag.
- Dane źródłowe należy oddzielić od cech wyliczonych.
4. Graf i wykrywanie kampanii
Dla NASK proponuję rozdzielić graf dowodów od grafu podobieństw.
Graf dowodów — przykład

Uproszczona wizualizacja fikcyjnych relacji: reklamy łączą się przez domeny, identyfikatory i zasoby.
Graf powinien umożliwiać zobaczenie, przez jakie konkretne obiekty reklamy są powiązane, a nie wyłącznie rysować linię pomiędzy reklamami.

Proponuję dwa poziomy:

Poziom 1 — graf heterogeniczny. Węzłami są reklamy, domeny, trackery, zasoby, hashe lub inne artefakty. Relacje mają znaczenia takie jak REDIRECTS_TO, USES_TRACKER, SHARES_ASSET.

Poziom 2 — graf reklam. Węzłami są tylko reklamy, a krawędzie reprezentują wyliczone podobieństwo i dowody. To ten graf wykorzystujemy do wykrywania klastrów.

Do klastrowania możecie zastosować Leiden lub Louvain. Przykładowo Leiden można uruchamiać na ważonym grafie, w którym krawędzie odpowiadają potwierdzonym kandydatom do powiązań. Nie traktujcie jednak automatycznie każdego komponentu jako jednej kampanii.

Przyda się także kategoria unassigned, dla reklam nieposiadających wystarczających dowodów.

5. Jak prezentować dane i powiązania?
Proponuję cztery główne widoki. Nie budowałbym kilkunastu zakładek — najważniejsze, żeby analityk mógł szybko przejść od pojedynczej reklamy do większej kampanii i zrozumieć, skąd pochodzą połączenia.
Widok 1 — Campaign Explorer
Główna strona aplikacji. Pokazuje wykryte kampanie, liczbę powiązanych reklam, domeny oraz najistotniejsze odkrycia.
Campaign Explorer
Poglądowe dane demonstracyjne

3 campaigns

124
Ads


18
Domains


3
Clusters



Investment Scam Network
Review

52 ads

8 domains

12 shared artifacts


Wspólne identyfikatory, podobne narracje o inwestowaniu i powtarzające się szablony.

Crypto Giveaway Network
43 ads

6 domains


Fake Store Network
29 ads

4 domains



Wymagania UX: filtrowanie po domenie, typie kampanii, czasie oraz poziomie dowodów. Kliknięcie w kampanię otwiera szczegółowy graf. Liczby powinny odzwierciedlać aktualny zakres filtrów.
Widok 2 — Investigation Graph
To powinien być najbardziej charakterystyczny ekran Methadata.

Graf nie może być tylko ozdobą. Ma pozwalać analizować relacje.

Proponowane interakcje:
- Kliknięcie w węzeł otwiera szczegóły reklamy, domeny lub artefaktu.
- Kliknięcie w krawędź pokazuje dowody połączenia.
- Zmiana progu podobieństwa ukrywa słabsze relacje.
- Filtry pozwalają wyświetlać tylko określone typy dowodów.
- Poziom szczegółowości można zmieniać między kampaniami, reklamami i artefaktami.
Interaktywny przykład filtrowania grafu
Relationship Graph

6 connections


Similarity threshold65%



[x] 
URL


[x] 
Text


[x] 
Image




Przykładowy graf reklam; w docelowym systemie typy relacji i wyniki muszą pochodzić z obliczonych dowodów.
Na początek użyłbym Cytoscape.js — obsługuje grafy, style, layouty i interakcje. React Flow wybrałbym raczej do wizualizacji pipeline'u niż do analizy większych sieci powiązań.

Dla dużych kampanii potrzebne będą agregacja, wyszukiwanie węzłów, filtrowanie i możliwość rozwijania sąsiedztwa zamiast ładowania wszystkich artefaktów naraz.
Widok 3 — Evidence Inspector
Moim zdaniem to najważniejszy element odróżniający Waszą aplikację od zwykłej wizualizacji grafu.

Użytkownik klika połączenie między dwiema reklamami i widzi dokładnie, dlaczego algorytm je powiązał.
Relationship Evidence
Strong similarity

Ad #1024

Investment campaign

Ad #1078

Investment campaign



Example weighted similarity
87%

Shared tracking ID

Strong

Both materials contain the same illustrative tracker value: GTM-EXAMPLE1

Similar landing pages

92%

Semantic similarity

81%

Visual similarity

76%



Dane demonstracyjne. Podobieństwo nie stanowi dowodu, że obie reklamy pochodzą od tego samego operatora.

W wersji produkcyjnej każdy dowód powinien zawierać również:
- Surową wartość i źródło, z którego została pozyskana.
- Datę pozyskania.
- Metodę i wersję algorytmu.
- Informację, czy jest bezpośrednim artefaktem, czy tylko wynikiem podobieństwa.
- Możliwość oznaczenia go jako niepoprawny.
Widok 4 — Campaign Timeline
Oś czasu powinna pokazywać ewolucję kampanii.
Campaign Evolution
Przykład fikcyjny

1. 10 listopada
   
   Pojawia się pierwsza reklama
   
   Materiał promujący inwestycje z wykorzystaniem AI.
2. 12 listopada
   
   Nowa domena docelowa
   
   Inny landing page, ale ten sam charakterystyczny identyfikator.
3. 15 listopada
   
   Zmiana kreacji reklamowej
   
   Nowy obraz i slogan, podobny szablon strony.
4. 18 listopada
   
   Rozpoznano potencjalne powiązanie
   
   Nowa reklama zostaje przypisana do analizy istniejącej kampanii.

Rozróżniajcie datę publikacji reklamy od daty jej wykrycia przez Wasz system. Brak dostępnej daty publikacji nie powinien powodować wymyślania jej na podstawie innych obserwacji.

6. Wymagania dla backendu i modeli danych
Proponuję następujące główne encje:
Encja	Co przechowuje?
Advertisement	Materiał, tekst, obraz, źródło, daty
Artifact	URL, domena, tracker, hash zasobu
AdArtifact	Powiązanie reklamy z artefaktem
FeatureVector	Embeddingi i cechy wyliczone
SimilarityEdge	Wynik podobieństwa dwóch reklam
Evidence	Uzasadnienie danej relacji
Campaign	Wykryty lub zatwierdzony klaster
CampaignMembership	Przynależność reklamy do kampanii
AnalysisRun	Konfiguracja, wersje modeli i przebieg analizy

Oddzielcie Campaign od AnalysisRun, aby ponowne obliczenie klastrów nie nadpisywało historii decyzji analityka.

Warto przyjąć także kilka niefunkcjonalnych wymagań dla MVP:
- Testowy import minimum 100–200 reklam z możliwością ponownego przetworzenia.
- Idempotentne importy i wykrywanie duplikatów.
- Każda relacja możliwa do odtworzenia z zapisanych danych.
- Obliczenia wsadowe zamiast ponownej analizy wszystkich par przy każdym dodaniu reklamy.
- Brak obowiązkowych zewnętrznych płatnych API.
- Izolowana analiza nieznanych URL-i.
- Brak wymogu podawania pewnej tożsamości osoby lub operatora kampanii.
Przy 200 reklamach pełne porównanie parami obejmuje 19 900 par, co jest rozsądne dla hackathonowego MVP. Przy większej skali przejdziecie na wyszukiwanie przybliżonych najbliższych sąsiadów (ANN), np. FAISS lub pgvector z indeksem HNSW, i będziecie liczyć droższy scoring tylko dla kandydatów.

7. Jak mierzyć sukces?
Przygotujcie oznaczony dataset z prawdziwymi pozytywami, trudnymi negatywami i przypadkami nieznanymi.
Test	Kryterium zaliczenia MVP
Extraction	OCR i metadane zapisują się w poprawnym schemacie
Similarity	Znana para podobnych reklam otrzymuje odpowiednio wysoki wynik
Retrieval	Recall@5 na kontrolowanym zbiorze osiąga np. 80%
Clustering	Pairwise F1 jest lepszy niż baseline tekstowy
Explainability	Każda relacja ma co najmniej jeden zapisany dowód lub jasno wskazany wynik modelowy
Negative examples	Podobny motyw nie powoduje automatycznego połączenia niezależnych reklam
New ad	Dodana reklama jest porównywana z istniejącymi i pojawia się w UI
Reproducibility	Ponowna analiza przy tej samej konfiguracji daje zgodne wyniki

80% to proponowany cel testowy, a nie gwarantowana wydajność. Wyniki powinny być raportowane osobno dla danych syntetycznych i rzeczywistych.
8. Co implementować w pierwszej kolejności?
Priorytet na hackathon
Najpierw: similarity + evidence

Import, OCR, embedding tekstowy, analiza podstawowych URL-i, obliczanie relacji i zapis dowodów.


Następnie: graf i porównywarka

Możliwość wejścia w relację i wyświetlenia szczegółowego uzasadnienia.


Jeśli zostanie czas: CLIP, timeline i automatyczne odkrywanie

Rozszerzenia, które są wartościowe, ale nie mogą blokować podstawowej analizy.



Gdybym miał wybrać trzy rzeczy, na których powinniście wygrać ten hackathon, byłyby to:
1. Explainable similarity — każde połączenie reklam da się uzasadnić konkretnymi dowodami.
2. Multi-signal correlation — pokazujecie, że tekst, obraz i infrastruktura dają lepsze wyniki razem niż osobno.
3. Campaign investigation graph — analityk potrafi przejść od jednej reklamy do całej potencjalnej kampanii.
To jest silniejszy przekaz niż samo „nasze AI wykrywa oszustwa”. Pokazujecie system, który pomaga odkrywać strukturę kampanii i przedstawia weryfikowalne przesłanki.

Moim zdaniem Evidence Inspector + graf powiązań powinny być centralnymi elementami demo. Reszta interfejsu powinna służyć właśnie temu procesowi badawczemu.