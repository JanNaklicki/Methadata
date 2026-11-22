# Baza materiałów (eksplorator)

> ⚠️ DEGRADED: single-context (przegląd wykonany w jednym kontekście, bez izolowanych subagentów A/B; detektor uruchomiony osobno na zmienionych plikach).

Zrzut „przed”: `../before/02-collection-explorer.png`

## Ocena
Najlepszy ekran pod względem hierarchii (grupy z miniaturami), ale wokół niego jest dużo surowych danych i niespójnych etykiet.

## Znaleziska
- **P1 – surowe identyfikatory**: `cl-20b8aabaea05` pod każdą grupą nic nie znaczy dla użytkownika.
- **P1 – niespójne etykiety filtrów**: „Szukaj w kolekcji”, „Data importu” z opcją „Dowolna data”, „Poziom podobieństwa” z opcją „Dowolny wynik”, „Każda domena”. Cztery filtry, cztery różne wzorce „wszystko”.
- **P1 – tabela „Materiały bez grupy”**: kolumna STAN powtarza „Bez grupy” w każdym wierszu (tytuł sekcji mówi to samo); długie nazwy domen łamią się w środku haszy.
- **P2 – „Ostatnio zaimportowane”**: 4 wpisy z identycznym znacznikiem czasu w monospace – dane bez informacji.
- **P2 – żargon**: „Bezpośrednie relacje”, „Wspólne artefakty”, „Najbliższa para”.
- **P3 – „Ostatnie importy”** to rozwijana lista bez kontekstu.

## Zmiany
- Usunięto identyfikatory grup z kart.
- Jeden wzorzec filtrów: „Szukaj”, „Domena / Wszystkie”, „Data importu / Cały okres”, „Podobieństwo / Dowolne”.
- STAN pokazuje powód („Za mało danych” albo „Brak podobnych materiałów”); domeny przycinane wielokropkiem.
- Lista ostatnich importów pokazuje samą datę, bez monospace.
- Nazewnictwo: „Powiązania”, „Wspólne ślady”, „Najwyższe podobieństwo”.


Zrzut „po”: `../after/02-collection-explorer.png`
