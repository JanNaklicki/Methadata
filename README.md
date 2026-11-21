# Methadata

## Start (Docker)

Wymagany Docker z Compose v2 (Docker Desktop albo Engine + wtyczka compose). Nie trzeba pliku `.env`, prywatnego
korpusu (BACKUP, scam_links) ani ręcznego importu danych: backend sam zasiewa syntetyczną próbkę demonstracyjną.

    docker compose up --build      # start (pierwsze budowanie pobiera obrazy i zależności)
    docker compose down            # zatrzymanie (dane zostają w wolumenie `methadata-data`)
    docker compose down -v         # zatrzymanie i usunięcie danych
    docker compose logs -f         # logi obu usług (`logs -f backend`, `logs -f frontend`)

- Podgląd: http://localhost:5173 (frontend; żądania `/api` i `/static` przechodzą przez proxy Vite do `backend:8000`).
- API do inspekcji: http://localhost:8000 (tylko localhost), health: http://localhost:5173/api/v1/health.
- Dane backendu (magazyn, przebiegi) leżą w nazwanym wolumenie `methadata-data`; przebiegi przeżywają `down` i ponowny start.

## Scenariusz jury

Profil `jury` korzysta z lokalnych zapisów stron `BACKUP/p2`–`BACKUP/p14`. Ustaw `METHADATA_BACKUP`, jeśli folder `BACKUP` znajduje się poza repozytorium. Polecenie przygotowania tworzy indeks i magazyn w `var/jury`. Z `BACKUP/p15` tworzy osobny pakiet ZIP z HTML, zasobami strony, HAR i zrzutem ekranu. Wymagane są Python i Docker Compose 2.24.4 lub nowszy.

```sh
pip install -e '.[web,media,pdf]'
python -m methadata_api.prepare_jury --data-dir var/jury
docker compose -p methadata-jury -f compose.yaml -f configs/demo/compose.jury.yaml up --build -d --wait
```

1. Na http://localhost:5173 wybierz `var/jury/p15.zip` i kliknij **Analizuj plik**.
2. W grupie zawierającej `p15.zip` odszukaj `p2` i `p7`. Na początku listy **Dowody** otwórz oba porównania przyciskiem **Pokaż dowody**. Każde zawiera grafikę z obu materiałów oraz uzasadnienie wyniku.
3. W nowej analizie wpisz `https://example.invalid/jury` i kliknij **Analizuj adres**. Pojawi się wynik **Za mało danych do rozstrzygnięcia**. Grupy z wcześniejszych obserwacji indeksu nadal są widoczne.
4. Zachowaj adres przebiegu lub dowodu. Wykonaj `docker compose -p methadata-jury -f compose.yaml -f configs/demo/compose.jury.yaml restart backend`, a po starcie usługi otwórz ten sam adres.

Serwer przyjmuje oba profile w `POST /api/v1/runs`. Nakładka Compose ustawia `METHADATA_ANALYSIS_PROFILE=jury` dla plików i adresów oraz wskazuje przygotowany magazyn przez `METHADATA_JURY_DATA_DIR`. Przycisk **Uruchom przykład** wybiera profil `demo` i używa osobnego magazynu z syntetyczną próbką. Bez nakładki wszystkie analizy używają domyślnie tej próbki. Pliki w `var/jury` i wolumen projektu przechowują dane potrzebne do odtworzenia przebiegów.

[Konfiguracja jury](configs/demo/jury.json) wskazuje profil `kampania` z F06. Grafiki, tekst i DOM mają równe wagi, próg wyniku i minimum pokrycia wynoszą 0,5, a krawędź wymaga wsparcia dwóch niezależnych rodzin. Przy p3/p4 wsparcie pochodzi z tekstu i DOM, co wskazuje na podobieństwo narracji i szablonu. Do ustalenia wspólnego nadawcy potrzeba osobnych dowodów. Wyniki dotyczą lokalnego zestawu znanych przykładów. Analiza używa zapisanych plików i działa bez pobierania stron lub ich zasobów.

### Test przeglądarkowy

Uruchom na syntetycznym profilu [scenariusze W04](tests/web-e2e/README.md), a następnie zatrzymaj tamte kontenery, aby zwolnić porty. Przy uruchomionym profilu jury wykonaj z korzenia repozytorium:

```sh
JURY_UPLOAD="$PWD/var/jury/p15.zip" JURY_SNAPSHOT="$PWD/var/jury/replay.json" \
  npm --prefix tests/web-e2e test -- jury-flow.spec.ts

docker compose -p methadata-jury -f compose.yaml -f configs/demo/compose.jury.yaml restart backend
docker compose -p methadata-jury -f compose.yaml -f configs/demo/compose.jury.yaml up -d --wait
JURY_REPLAY=1 JURY_SNAPSHOT="$PWD/var/jury/replay.json" \
  npm --prefix tests/web-e2e test -- jury-flow.spec.ts
```

W próbie R02 na lokalnym korpusie `backup-p2-p14+zgłoszenie@8557e8dcf1b2bafc` uzyskano wyniki 0,999364 dla p15–p2 i 0,999402 dla p15–p7. Chromium załadował grafiki po obu stronach obu dowodów. Dla samego URL-u wszystkie 13 porównań zgłoszenia było niewykonalnych. Po restarcie API zwróciło identyczny przebieg i oba dowody. Scenariusze W04 z przykładem, plikiem, URL-em i awarią backendu wykonano na próbce syntetycznej.

## Rozwój bez Dockera

Backend (port 8000):

    pip install -e '.[web,media,pdf]'
    METHADATA_DATA_DIR=var/methadata-data python -m methadata_api

Frontend (port 5173, proxy na `http://localhost:8000`; `BACKEND_URL` zmienia cel, `MOCK_API=1` uruchamia atrapę API):

    cd frontend && npm ci && npm run dev

Testy: `pip install -e '.[test,media,pdf,ocr,web]' && pytest`; test zgodności API:
`python contracts/http/check_conformance.py --base-url http://localhost:8000`.
