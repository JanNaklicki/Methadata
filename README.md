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

## Rozwój bez Dockera

Backend (port 8000):

    pip install -e '.[web,media,pdf]'
    METHADATA_DATA_DIR=var/methadata-data python -m methadata_api

Frontend (port 5173, proxy na `http://localhost:8000`; `BACKEND_URL` zmienia cel, `MOCK_API=1` uruchamia atrapę API):

    cd frontend && npm ci && npm run dev

Testy: `pip install -e '.[test,media,pdf,ocr,web]' && pytest`; test zgodności API:
`python contracts/http/check_conformance.py --base-url http://localhost:8000`.
