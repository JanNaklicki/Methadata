# Graf powiązań

> ⚠️ DEGRADED: single-context (przegląd wykonany w jednym kontekście, bez izolowanych subagentów A/B; detektor uruchomiony osobno na zmienionych plikach).

Zrzut „przed”: `../before/03-graph.png`

## Ocena
Narzędzia grafu są kompletne, ale opisane językiem implementacji. Zrzut powstał w przeglądarce bez WebGL 2, więc widać widok zastępczy – i właśnie on jest dziś najsłabszy.

## Znaleziska
- **P1 – komunikat zastępczy**: „Graf wymaga WebGL 2. Możesz przeglądać dane w poniższej liście.” – techniczny, bez wyjścia.
- **P1 – etykiety**: „Próg podobieństwa” (w eksploratorze to „Poziom podobieństwa”), „Ślady wspierające relację”, „Sieć materiałów 15 mat. · 8 relacji” (skrót „mat.”), „Pokaż całość” (resetuje też filtry).
- **P2 – „Graf podobieństw / Graf dowodów”**: dwa „grafy” o niejasnej różnicy.
- **P2 – „Brak zaakceptowanych powiązań”** pod każdym materiałem – język algorytmu.
- **P3 – „Szczegóły materiału”** w pustym stanie: długi opis zamiast jednej instrukcji.

## Zmiany
- Spójne nazwy: „Minimalne podobieństwo”, „Typ śladu”, „Powiązania”, „Resetuj widok”.
- Zakładki: „Podobieństwa” / „Wspólne ślady”.
- Komunikat zastępczy opisuje sytuację i mówi, że lista jest tą samą zawartością.
- „Bez powiązań” zamiast „Brak zaakceptowanych powiązań”.


Zrzut „po”: `../after/03-graph.png`
