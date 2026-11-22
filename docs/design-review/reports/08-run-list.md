# Wynik analizy (lista)

> ⚠️ DEGRADED: single-context (przegląd wykonany w jednym kontekście, bez izolowanych subagentów A/B; detektor uruchomiony osobno na zmienionych plikach).

Zrzut „przed”: `../before/08-run-list.png`

## Ocena
Najgorszy ekran: 941 par w jednej liście, strona ma ok. 15 000 px wysokości. To „czytanie surowych danych” w czystej postaci.

## Znaleziska
- **P0 – 941 wierszy** po rozwinięciu „Pozostałe porównania”; DOM jest budowany także przed otwarciem.
- **P1 – nagłówek** „Przebieg analizy run-…” i „Grupa cl-…” – identyfikatory zamiast nazw.
- **P1 – „Szum”** w statystykach i „Poza grafem” przy parach – język algorytmu.
- **P2 – „Metoda fusion-v1”** pod każdym uzasadnieniem.

## Zmiany
- „Pozostałe porównania” renderowane dopiero po rozwinięciu, po 30 sztuk z przyciskiem „Pokaż więcej”.
- „Wynik analizy”; grupy numerowane („Grupa 1”).
- „Bez dopasowania”, „Nie połączone”; usunięty wiersz z metodą.


Zrzut „po”: `../after/08-run-list.png`
