# Kolekcjoner Fejków - źródło prawdy

> Status: obowiązujący dokument roboczy.  
> Źródło: `NASK - Pełna treść zadania konkursowego.pdf` (pełny opis zadania).  
> Jeśli decyzja projektowa stoi z nim w sprzeczności, wygrywa ten dokument oraz oryginalne zadanie.

## 1. Cel produktu

Zbudować demonstrator, który analizuje podejrzane reklamy lub materiały i **łączy wiele pozornie niezależnych materiałów w większe kampanie** na podstawie pozostawionych przez nie wspólnych śladów.

Produkt nie jest oceniany jako idealny detektor pojedynczego fejka. Najważniejsze jest pokazanie, że potrafimy:

- wydobyć cechy materiałów;
- rozpoznać ich podobieństwo;
- utworzyć sensowne klastry kampanii;
- wyjaśnić, jakie sygnały połączyły materiały.

## 2. Kryterium sukcesu demo

Na danych demonstracyjnych użytkownik podaje materiał, uruchamia analizę i widzi:

1. wyodrębnione sygnały;
2. najbardziej powiązane materiały lub kampanię;
3. uzasadnienie połączeń, np. wspólną domenę, przekierowanie, narrację, osobę, obraz, szablon lub słowa kluczowe;
4. reprezentację relacji między materiałami.

## 3. Wymagania obowiązkowe

- [ ] Jest prosta działająca strona demonstracyjna z graficznym środowiskiem uruchomieniowym.
  - Estetyka strony nie jest oceniana.
  - To kryterium binarne: demonstrator jest albo go nie ma.
- [ ] Strona przyjmuje co najmniej jeden sensowny punkt wejścia do analizy. Dopuszczalne wejścia z zadania:
  - bezpośredni URL;
  - zrzut ekranu zawierający link;
  - nazwa produktu lub inna wskazówka;
  - plik z podejrzaną reklamą.
- [ ] System analizuje materiał w zakresie przydatnym dla zadania.
- [ ] System wyznacza podobieństwo materiałów i grupuje je w kampanie/klastry.
- [ ] Demo pokazuje, dlaczego materiały trafiły do tego samego klastra.

## 4. Sygnały do analizy

Zakres jest dowolny - liczy się użyteczność mechanizmów. Rozważamy i priorytetyzujemy:

- tekst, narrację i słowa kluczowe;
- elementy wizualne i podobieństwo obrazów;
- osoby występujące w materiale;
- adresy URL, domeny i ich metadane;
- przekierowania i odnośniki;
- wyizolowane strony docelowe;
- motyw przewodni / model kampanii;
- podobny szablon produkcji;
- wspólny zestaw drobnych sygnałów, nawet gdy pojedynczo są słabe.

**Zasada:** nie traktować jednego słabego wskaźnika jako dowodu. Wartość ma kombinacja śladów oraz czytelne uzasadnienie jej wpływu na wynik.

## 5. Podobieństwo i klastrowanie - rdzeń rozwiązania

- [ ] Każdy materiał ma ustrukturyzowany zestaw cech.
- [ ] System oblicza podobieństwo między materiałami.
- [ ] Mechanizm może być ważony, modelowy lub klastrujący; technologia jest dowolna.
- [ ] Wynik łączy materiały w kampanie, a nie tylko zwraca pojedynczy score.
- [ ] Dla każdej relacji/klastra widoczne są cechy, które ją wspierają.
- [ ] Mechanizm pozwala rozpoznać kolejne warianty już znanej narracji lub źródła.

## 6. Tożsamość osób

- [ ] Identyfikacja osoby widocznej w materiale może być rozwiązana dowolnie.
- [ ] Do demo wystarcza niewielka referencyjna baza kilku najczęściej pojawiających się osób.
- [ ] Rozmiar tej bazy nie jest kryterium oceny; ma dowieść użyteczności narzędzia.

## 7. Prezentacja powiązań

- [ ] Użytkownik widzi relacje między reklamami, cechami i kampaniami.
- [ ] Forma jest dowolna (np. graf relacji, lista uzasadnionych połączeń, widok klastra).
- [ ] Neo4j jest opcją, nie wymogiem.

## 8. Opcjonalny element wzmacniający demo

- [ ] Scheduler okresowo skanuje zidentyfikowane biblioteki reklam, wykrywa podejrzane materiały i uruchamia ich analizę.

To element dodatkowo pozytywny, nie warunek podstawowy.

## 9. Zasady podejmowania decyzji w projekcie

1. Priorytet: jakość łączenia kampanii i wytłumaczalność połączeń.
2. Preferujemy mały, działający przepływ end-to-end nad rozbudowane, niepołączone moduły.
3. Każdą funkcję oceniamy pytaniem: „Czy pomaga wykryć wspólny ślad między materiałami?”
4. W demo pokazujemy kilka różnych rodzajów dowodów - nie tylko tekst albo tylko obraz.
5. Odporność na warianty materiału jest ważniejsza niż perfekcyjna klasyfikacja pojedynczego przypadku.
6. Rozwiązanie ma pozostać demonstratorem: ograniczona, kontrolowana baza danych jest akceptowalna, jeśli jasno dowodzi działania metody.

## 10. Checklist przed oddaniem

- [ ] Aplikacja uruchamia się lokalnie według krótkiej instrukcji.
- [ ] Jest dostępny prosty interfejs graficzny.
- [ ] Można przekazać materiał do analizy.
- [ ] Analiza tworzy zestaw cech.
- [ ] Materiał zostaje porównany z bazą przykładów.
- [ ] Wynik wskazuje klaster/kampanię lub najbliższe relacje.
- [ ] Uzasadnienie wyniku wskazuje konkretne wspólne ślady.
- [ ] Widoczna jest reprezentacja powiązań.
- [ ] Demo zawiera co najmniej jeden przekonujący przypadek połączenia wielu materiałów.
- [ ] Opcjonalnie: działa scheduler do automatycznego skanowania bibliotek reklam.

## 11. Poza zakresem / nie mylić z celem

- Nie musimy stworzyć idealnego detektora pojedynczej fałszywej reklamy.
- Nie musimy zbudować dużej bazy znanych osób.
- Nie musimy używać konkretnego modelu, silnika klastrującego ani Neo4j.
- Nie oceniamy projektu po estetyce demonstratora, ale interfejs musi istnieć i umożliwiać uruchomienie analizy.

## 12. Mini-lista: narzędzia i źródła warte rozważenia

To są kandydaci do proof-of-conceptu, nie wymagania konkursowe. Wybieramy tylko te, które wzmacniają wykrywanie wspólnych śladów i dają się pokazać w demo.

- **Ekstrakcja śladów ze strony docelowej** - pobranie HTML/DOM, tytułu i metadanych, tekstu, linków, parametrów kampanii (np. `utm_*`), identyfikatorów trackerów/pikseli, zasobów graficznych oraz łańcucha przekierowań. Przykładowe archiwa fejkowych stron już pokazują obecność parametrów UTM i metadanych, więc jest to tani, objaśnialny sygnał do grafu powiązań.
- **OCR, embeddingi tekstu i hash/embedding obrazu** - wydobycie narracji oraz podobieństw między zrzutami, kreacjami i stronami docelowymi; szczególnie użyteczne przy zmienionych domenach, lecz tym samym szablonie kampanii.
- **Analiza głosu i syntetycznej mowy** - dla reklam wideo/audio: transkrypcja, embedding głosu do porównania mówców oraz osobny score „spoof/deepfake”. Artykuł Martinek, Gajewskiej i Bartuzi-Trokielewicz porównuje m.in. SpeechBrain i Resemblyzer (podobieństwo mówcy) oraz MFA-Conformer i SKA-TDNN (modele spoof-aware). Wynik należy traktować jako sygnał pomocniczy: skuteczność wyraźnie spada przy mieszaniu mowy czytanej i spontanicznej.
- **Meta Ad Library** - źródło kandydatów do monitorowania i rozszerzania klastra: reklamy, ich warianty kreatywne, reklamodawcy oraz dane o emisji dostępne w bibliotece. Dobry kandydat do opcjonalnego schedulera z sekcji 8.

**Proponowany minimalny zestaw demo:** URL/plik -> ekstrakcja strony i OCR -> cechy tekstu, obrazu, domeny oraz przekierowań -> ważone podobieństwo -> graf z wyjaśnieniem. Moduł audio i automatyczne skanowanie Meta Ad Library dokładamy tylko, jeśli nie osłabi to działającego przepływu podstawowego.
