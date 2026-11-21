# Test przeglądarkowy W04

Playwright w Chromium otwiera frontend Vite po HTTP. Żądania przeglądarki przechodzą przez proxy Vite do backendu Python. Test dodatkowo odczytuje zapisany wynik i dowód bezpośrednio z API i porównuje je z widokiem.

- Przykład syntetyczny zawiera materiały A, B i C. A i C tworzą jedną grupę, a B pozostaje poza nią. Test porównuje `run_id` z API, otwiera dowód z grafiką po obu stronach i odświeża adres dowodu oraz adres przebiegu.
- Drugi materiał to syntetyczny HTML przesłany jako plik `dowolna nazwa (1).dat`. Test sprawdza, że materiał trafia do wyniku.
- Adres w domenie `example.invalid` jest analizowany offline. Test sprawdza wynik „Za mało danych do rozstrzygnięcia” i brak ocenionych porównań tego materiału.
- Po zatrzymaniu backendu test przykładu ma się nie powieść, a osobny test sprawdza komunikat widoczny na stronie głównej.

## Uruchomienie

Użyj Node.js 22 i działającego polecenia `docker compose`. Wykonaj poniższe polecenia z korzenia repozytorium. Projekt `methadata-w04-e2e` ma własny wolumen. Aby rozpocząć z czystym wolumenem, użyj nowej nazwy projektu, jeśli ta była wcześniej używana. Porty hosta 5173 i 8000 muszą być wolne.

```sh
docker compose -p methadata-w04-e2e up --build -d --wait
npm --prefix tests/web-e2e ci
npm --prefix tests/web-e2e run install-browser
PLAYWRIGHT_HTML_OUTPUT_DIR=playwright-report-baseline \
  npm --prefix tests/web-e2e test -- --output=test-results-baseline
```

`WEB_URL` wskazuje frontend (domyślnie `http://localhost:5173`), a `API_URL` wskazuje backend (domyślnie `http://127.0.0.1:8000`). W standardowym uruchomieniu Playwright wykonuje trzy scenariusze i pomija test zatrzymanego backendu.

## Zatrzymanie i przywrócenie backendu

Pozostaw kontener frontendu uruchomiony. W kroku z `EXPECT_BACKEND_DOWN=1` oczekiwany jest kod wyjścia 1. W raporcie tej próby scenariusz „przykład” ma zgłosić błąd HTTP na `GET /api/v1/demo`, a scenariusz „niedostępne API” ma przejść.

```sh
docker compose -p methadata-w04-e2e stop backend
EXPECT_BACKEND_DOWN=1 PLAYWRIGHT_HTML_OUTPUT_DIR=playwright-report-outage \
  npm --prefix tests/web-e2e test -- --grep 'przykład|niedostępne API' --output=test-results-outage
docker compose -p methadata-w04-e2e logs --no-color > tests/web-e2e/test-results-outage/compose.log
docker compose -p methadata-w04-e2e start --wait backend
npm --prefix tests/web-e2e test
```

Raporty HTML pod `tests/web-e2e/` to `playwright-report-baseline/index.html`, `playwright-report-outage/index.html` i `playwright-report/index.html`. Załącznik `diagnostyka.txt` zawiera statusy HTTP żądań API i podglądów, nieudane żądania, logi konsoli i błędy strony. Przy porażce raport zachowuje ślad i zrzut ekranu. Polecenie `docker compose logs` zapisuje także logi Vite i backendu.

Po zakończeniu możesz zwolnić porty poleceniem `docker compose -p methadata-w04-e2e down`.

## Wynik weryfikacji W04

Docker Engine 29.4.0, Compose v5.1.1, Node.js 22.22.2 i Chromium z Playwright 1.65.1. Projekt wystartował na nowym wolumenie z danymi demonstracyjnymi.

| Stan backendu | Wynik Playwright | Kod wyjścia |
| --- | --- | ---: |
| Działa, nowy wolumen | 3 zaliczone, test awarii pominięty (4,3 s) | 0 |
| Zatrzymany | Test przykładu zgłosił HTTP 502. Test komunikatu w UI zaliczony (2,0 s). | 1 |
| Przywrócony | 3 zaliczone, test awarii pominięty (3,8 s) | 0 |

Weryfikacja obejmuje Chromium i syntetyczne dane demonstracyjne.
