# Przegląd UX ekranów (impeccable critique + polish)

Raporty: `reports/`. Zrzuty 1440×900: `before/` i `after/`. Wspólny wniosek: aplikacja pokazywała dane tak, jak przychodzą z API
(identyfikatory, skróty SHA-256, 941 par w jednej liście, nazwy metod). Zmiany: dane techniczne zwinięte, jedno nazewnictwo
(materiał / powiązanie / ślad / dowód), spójne filtry, stronicowanie długich list, usunięty zbędny tekst.

Zrzuty ekranu (`before/`, `after/`) nie są w repozytorium, bo zawierają miniatury materiałów organizatora. Odtworzysz je skryptem
Playwright na własnej kopii danych (adresy ekranów: `/`, `/collection`, `/collection/graph`, `/collection/evidence`, `/collection/timeline`, `/collection/files`, `/runs/<id>`).
