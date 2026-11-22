# Inspektor dowodów

> ⚠️ DEGRADED: single-context (przegląd wykonany w jednym kontekście, bez izolowanych subagentów A/B; detektor uruchomiony osobno na zmienionych plikach).

Zrzut „przed”: `../before/05-evidence-inspector.png`

## Ocena
Górna część (zdjęcia, wynik, sygnały) działa. Dolna połowa strony to tabela 40 wierszy z SHA-256 – najbardziej „surowy” fragment całej aplikacji.

## Znaleziska
- **P0 – rejestr artefaktów** wystawiony na wierzchu: identyfikatory `can-…`, `art-…`, 64-znakowe skróty.
- **P1 – metadane wprost z API**: „resource · image/jpeg”, „wynik: 1.00 · metoda fusion-v1”.
- **P1 – duplikat**: para „p2 ↔ p7” i wynik pojawiają się w liście wyboru, nagłówku i sekcji pochodzenia.
- **P2 – nazwy sekcji**: „Progi i metoda”, „Wyniki dodatkowych metod”, „Metody poza tym przebiegiem”.

## Zmiany
- Pochodzenie dowodu zwinięte do „Szczegóły techniczne (N plików)”; identyfikatory i tabela w środku.
- Role i formaty po polsku („Obraz · JPEG”).
- Usunięto linię „wynik: … · metoda …” (wynik jest wyżej).
- Zmienione nazwy sekcji: „Jak obliczono wynik”, „Dodatkowe porównania”.


Zrzut „po”: `../after/05-evidence-inspector.png`
