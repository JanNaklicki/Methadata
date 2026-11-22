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

### Tryb pełny w Dockerze (modele ML, wideo i audio)

Domyślny obraz jest lekki: bez modeli ML i bez `ffmpeg`, więc analiza grafik, tekstu, struktury stron, HAR i PDF działa od razu,
a wideo/audio i embeddingi są pomijane z jawnym oznaczeniem „brak”. Pełny zestaw (OCR, MiniLM, OpenCLIP, Whisper, SpeechBrain) włączysz przy budowaniu:

    METHADATA_EXTRAS='web,media,pdf,ocr,perceptual,semantic,speech,visual-embedding' \
    METHADATA_WITH_FFMPEG=1 METHADATA_COLLECTION_MODELS=1 docker compose up --build

Wagi modeli pobierają się przy pierwszym użyciu (kilkaset MB, zob. tabelę licencji poniżej) i wymagają dostępu do sieci.

## Baza materiałów

Otwórz `http://localhost:5173/collection` lub wybierz **Baza materiałów**. **Dodaj do bazy** przyjmuje
pojedyncze pliki, wiele plików lub ZIP z wieloma zapisami stron. Import ZIP z ekranu **Nowa analiza**
otwiera raport importu z postępem przetwarzania. W profilu jury małe ZIP-y nadal otwierają porównanie z
korpusem jury, a plik równolegle trafia do bazy materiałów.

Oryginały i rozpakowane pliki przechowują się w `METHADATA_DATA_DIR/collection`. W widoku **Pliki źródłowe**
można pobierać oryginały i wpisy z ZIP-u, zmieniać nazwy oraz archiwizować i przywracać pliki i materiały.
**Odśwież bazę** pobiera stan rejestru, importu i grafu. Gdy uruchomiony backend zwraca 404 dla
`/api/v1/collection`, uruchom aktualną wersję backendu na tym samym wolumenie danych.

Limit importu to domyślnie 128 MiB na plik. Zmienia go `METHADATA_COLLECTION_UPLOAD_BYTES`. Czytnik ZIP
przetwarza archiwa zagnieżdżone do trzech poziomów, do 5 000 wpisów i 1 GiB rozpakowanych danych na
drzewo ZIP. Raport pokazuje pominięte wpisy i stan analizy. Przerwany import wznawia się z oryginału, a
nieudany można ponowić z raportu.

Interfejs korzysta z `POST /api/v1/imports` (multipart, pole `files`, nagłówek `Prefer: respond-async`),
`GET /api/v1/imports/{id}` i `GET /api/v1/collection`. Numer wersji rośnie wraz ze zmianami aktywnego grafu.
Operacje rejestru opisuje [kontrakt HTTP](contracts/http/openapi.yaml).

Dodatkowe ślady obejmują pHash, bliskie kopie tekstu, zapisane przekierowania HAR oraz identyczne skrypty.
W środowisku lokalnym zainstaluj extra `perceptual` dla pHash. OCR, MiniLM, OpenCLIP i ORB można uruchomić
eksperymentalnie przez `METHADATA_COLLECTION_MODELS=1` z extras `ocr,semantic,visual-embedding,perceptual`
oraz wagami modeli w lokalnym cache. Dodatkowe ślady są dostępne w panelu materiału i nie zmieniają progów grupowania.

## Wejścia analizy

Ekran **Nowa analiza** przyjmuje trzy rodzaje wejścia, a **Baza materiałów** dodatkowo importy zbiorcze:

| Wejście | Co przyjmuje |
| --- | --- |
| **Plik** | zapis strony (HTML/MHTML), log komunikacji (HAR), zrzut ekranu (PNG/JPEG/WebP), PDF, wideo (MP4/WebM), audio (MP3/WAV/OGG), archiwum ZIP (także zagnieżdżone) |
| **Adres URL lub domena** | pełny adres albo sama domena, także długie linki reklamowe z trackerami; domyślnie bezpieczne pobranie strony wraz z przekierowaniami, a gdy strona nie odpowiada, zapisuje się sam adres i domena (analiza nie przerywa się) |
| **Wskazówka** | nazwa produktu, osoba, marka lub słowa kluczowe; przeszukuje treść zaimportowanych materiałów, domeny i wspólne ślady, a wynik otwiera w bazie materiałów (`/collection?q=…`) |

Brakujące dane nie zatrzymują analizy: materiał z samym adresem lub bez części cech jest porównywany tylko tam, gdzie
są dane, a interfejs pokazuje „Za mało danych” zamiast zgadywać wynik.

## Nowy materiał a istniejące kampanie

Po każdym imporcie raport wskazuje, co stało się z nowym materiałem: **dołączył do istniejącej grupy**, tworzy **nową grupę
(potencjalna nowa kampania)** albo zostaje **bez grupy**. Materiał, który nie ma wystarczających podobieństw, nie jest
łączony z żadną kampanią. Ponowny import tego samego zapisu jest rozpoznawany jako duplikat i nie dubluje węzłów.
Każde połączenie ma uzasadnienie: wynik na rodzinę cech (grafiki, tekst, struktura HTML), wspólne ślady i możliwość
sprawdzenia, jak wynik zmieni się po wyłączeniu rodziny (scenariusze co-jeśli).

## Skan źródła reklam (bonus)

W widoku **Pliki źródłowe** przycisk **Skanuj źródło** wykonuje jeden przebieg monitora (`methadata.monitor`) nad eksportem
JSON/JSONL i pokazuje, ile reklam jest nowych lub zmienionych od poprzedniego skanu oraz ile grup płatników ma nowe wpisy.
Domyślnie źródłem jest syntetyczny plik [configs/monitor/sample_feed.jsonl](configs/monitor/sample_feed.jsonl); własny eksport
wskazuje `METHADATA_MONITOR_FEED`. Dopisz rekord do pliku i skanuj ponownie, żeby zobaczyć tylko nowe wpisy. Nowe rekordy
trafiają do osobnego magazynu (`<dane>/monitor`) i nie zmieniają grafu kolekcji. Dostęp do Meta Ad Library API (token) pozostaje
w CLI (`python -m methadata.monitor run --meta-search …`, opis w [src/methadata/monitor/README.md](src/methadata/monitor/README.md)).

## Dane wychodzące i prywatność

Analiza działa lokalnie. Jedynie poniższe funkcje kontaktują się z zewnętrznymi usługami i każdą da się wyłączyć:

| Funkcja | Co opuszcza komputer | Wyłączenie |
| --- | --- | --- |
| Podsumowania grup (OpenAI `gpt-4o-mini`), **domyślnie wyłączone**; włącza je `METHADATA_ENABLE_LLM=1` wraz z `OPENAI_API_KEY` | zwarty opis grupy: tytuły i fragmenty tekstu (do 220 znaków), domeny, trackery, liczby i wyniki | nic nie trzeba robić; `METHADATA_DISABLE_LLM=1` wyłącza także przy ustawionym `METHADATA_ENABLE_LLM` (zostaje deterministyczne podsumowanie bez sieci) |
| Wzbogacanie domen (DNS, TLS, RDAP) | zapytania do publicznych nazw hostów z materiałów; połączenie TLS z serwerem hosta ujawnia adres IP analityka | `METHADATA_DISABLE_ENRICHMENT=1` |
| Pobranie strony z adresu URL | zapytanie HTTP do podanego adresu (bezpieczny fetch, bez adresów prywatnych) | tryb offline: odznacz „Pobierz stronę” |

Wzbogacanie domen nie wysyła żadnych zapytań do adresów prywatnych, loopback ani link-local, więc spreparowany HAR nie
posłuży do skanowania sieci wewnętrznej. Materiały udostępnione przez organizatora nie opuszczają komputera, dopóki sam nie włączysz podsumowań OpenAI. Testy automatyczne mają obie usługi wyłączone na stałe (`tests/conftest.py`).

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

Serwer przyjmuje oba profile w `POST /api/v1/runs`. Nakładka Compose ustawia `METHADATA_ANALYSIS_PROFILE=jury` dla plików i adresów oraz wskazuje przygotowany magazyn przez `METHADATA_JURY_DATA_DIR`. Przycisk **Uruchom przykład** wybiera profil `demo` i używa osobnego magazynu z syntetyczną próbką: materiały A i C to warianty jednej kampanii i tworzą grupę, a B (inny temat i szablon) zostaje osobno. Bez nakładki wszystkie analizy używają domyślnie tej próbki. Pliki w `var/jury` i wolumen projektu przechowują dane potrzebne do odtworzenia przebiegów.

[Konfiguracja jury](configs/demo/jury.json) wskazuje profil `kampania` z F06. Grafiki, tekst i DOM mają równe wagi, próg wyniku i minimum pokrycia wynoszą 0,5, a krawędź wymaga wsparcia dwóch niezależnych rodzin. Przy p3/p4 wsparcie pochodzi z tekstu i DOM, co wskazuje na podobieństwo narracji i szablonu. Do ustalenia wspólnego nadawcy potrzeba osobnych dowodów. Wyniki dotyczą lokalnego zestawu znanych przykładów. Porównanie z korpusem jury używa zapisanych plików, bez pobierania zasobów stron.

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

## Metodologia i pełny pakiet modeli

Szczegółowy opis architektury, ekstrakcji cech, wzorów podobieństwa, fuzji i obsługi niepełnych danych znajduje się w [docs/METHODOLOGY.md](docs/METHODOLOGY.md).

Uruchomienie backendu z pełnym zestawem modeli ML (OCR, MiniLM, OpenCLIP, Whisper, SpeechBrain) oraz podsumowaniami LLM:

```sh
pip install -e '.[web,media,pdf,ocr,graph,density,spectral,provenance,perceptual,semantic,speech,visual-embedding,test]'
METHADATA_DATA_DIR=var/methadata-data \
METHADATA_COLLECTION_MODELS=1 \
METHADATA_MODEL_CACHE="$HOME/.cache/huggingface/hub" \
python -m methadata_api
```

- **Dopasowanie wizualne osób referencyjnych:** umieść obrazy referencyjne (`Imie_Nazwisko.jpg` lub podkatalogi `Imie_Nazwisko/*.jpg`) w `var/reference_figures/`, `data/reference_figures/` (katalog w repozytorium, zob. jego README) albo wskaż własny przez `METHADATA_REFERENCE_DIR`. Zdjęcia w `data/reference_figures/` są domyślnie ignorowane przez git.
- **Monitorowanie Biblioteki Reklam (`methadata.monitor`):** cykliczny/przyrostowy skaner źródeł obsługujący zarówno lokalny eksport JSON (`JsonFileProvider` — tryb offline), jak i Meta Ad Library API (`MetaAdLibraryProvider`, zmienna `META_AD_LIBRARY_TOKEN`). Szczegóły i komendy CLI opisuje [src/methadata/monitor/README.md](src/methadata/monitor/README.md).

## Biblioteki, modele, licencje i dostęp do API

| Komponent | Przeznaczenie | Licencja | Ograniczenia / uzyskanie dostępu |
| --- | --- | --- | --- |
| **OpenAI API** (`gpt-4o-mini`) | Jednozdaniowe podsumowania grup kampanii (z cache SHA-256 i fallbackiem offline) | Warunki OpenAI API | Wymaga klucza `OPENAI_API_KEY` w `.env` (klucz z [platform.openai.com](https://platform.openai.com)); opcjonalnie `METHADATA_LLM_MODEL` lub `METHADATA_DISABLE_LLM=1`. |
| **Meta Ad Library API** (`methadata.monitor`) | Przyrostowe pobieranie rekordów reklam z Meta Ad Library | Meta Platform Terms | Wymaga tokenu `META_AD_LIBRARY_TOKEN` (zweryfikowane konto Meta Developer); dostępny też tryb offline na plikach JSON (`JsonFileProvider`). |
| **RDAP (`rdap.org`) / DNS / TLS** | Publiczne wzbogacanie domen (rejestrator, wiek domeny, IP, wystawca TLS) | Publiczne protokoły IETF | Bez klucza API; obowiązuje cache na dysku oraz limity zapytań; wyłączenie: `METHADATA_DISABLE_ENRICHMENT=1`. |
| **RapidOCR** (`rapidocr-onnxruntime`) | OCR ze zrzutów ekranu i grafik | Apache-2.0 | Działa lokalnie na CPU (ONNX Runtime). |
| **Sentence-Transformers** (`paraphrase-multilingual-MiniLM-L12-v2`) | Wielojęzyczne embeddingi akapitów | Apache-2.0 | Wagi pobierane z Hugging Face do lokalnego cache (~470 MB). |
| **OpenCLIP** (`ViT-B-32`, `laion2b_s34b_b79k`) + **PyTorch** | Embeddingi wizualne obrazów i dopasowanie osób referencyjnych | MIT / BSD-3-Clause | Wagi pobierane do `METHADATA_MODEL_CACHE` (~600 MB); działa na CPU/MPS. |
| **Faster-Whisper** (`tiny`) | Transkrypcja mowy z MP3/MP4/WAV/OGG | MIT | Wagi pobierane z Hugging Face (~75 MB); działa lokalnie na CPU (CTranslate2). |
| **SpeechBrain** (`spkrec-ecapa-voxceleb`) + **torchaudio** | 192-wymiarowy embedding głosu mówcy (ECAPA-TDNN) | Apache-2.0 | Wagi pobierane z Hugging Face (~90 MB); wynik traktowany jako sygnał pomocniczy. |
| **FFmpeg / FFprobe** | Dekodowanie kontenerów MP4/WebM/MP3/OGG i próbkowanie klatek | LGPL / GPL | Wymagany binarny `ffmpeg`/`ffprobe` w `PATH` (np. `brew install ffmpeg`). |
| **OpenCV**, **Pillow**, **pypdf**, **c2pa-python**, **scikit-learn**, **igraph/leidenalg** | pHash, ORB, kody QR, PDF, C2PA, klastrowanie grafowe i gęstościowe | Apache-2.0 / MIT / BSD / GPL-2+ | Instalowane jako opcjonalne extras w `pyproject.toml`. |
| **FastAPI**, **Pydantic**, **Uvicorn**, **httpx** | Backend HTTP API i bezpieczny fetcher | MIT / BSD-3-Clause | Wymagane dla serwera webowego (`extra: web`). |
| **React 19**, **Sigma.js 4**, **Graphology**, **ForceAtlas2**, **Vite** | Interfejs webowy i renderowanie grafu WebGL 2 | MIT | Zależności frontendu w `frontend/package.json`. |
