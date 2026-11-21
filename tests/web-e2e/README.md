# Test przeglądarkowy W04 (Playwright)

Scenariusze przeciwko uruchomionemu zestawowi Compose (prawdziwe HTTP: przeglądarka -> frontend -> proxy -> backend Python):
przykład -> wynik -> dowód obu stron -> odświeżenie adresu; plik o dowolnej nazwie; sam URL; niedostępne API.
Główny test porównuje `run_id` i skład grupy widoku z odpowiedzią `GET /api/v1/runs/{id}`.

    docker compose down -v                # opcjonalnie: czysta przestrzeń danych
    docker compose up --build -d          # z korzenia repo
    cd tests/web-e2e
    npm ci && npx playwright install chromium
    npm test                              # raport: playwright-report/index.html (npx playwright show-report)

Zmienne: `WEB_URL` (domyślnie http://localhost:5173), `API_URL` (domyślnie http://127.0.0.1:8000).
Statusy odpowiedzi /api i /static, logi konsoli i błędy strony dołączane są do raportu jako `diagnostyka.txt`
(przy porażce także na stdout); ślad i zrzut ekranu zachowywane przy porażce.

Test grupy A+C zakłada zestaw demo; pozostałe scenariusze tolerują dane po poprzednich przebiegach.
Kontrola "bez backendu": `docker compose stop backend` -> `npm test` musi zawieść (główny test); `docker compose start backend` -> przechodzi.
