"""Aplikacja FastAPI: operacje z contracts/http/openapi.yaml (W01) nad tym samym silnikiem co CLI."""
from __future__ import annotations

import logging
import re
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException

from methadata.contracts.models import InputKind, Observation
from methadata.ingest.detect import sniff
from methadata.intake import submit_files, submit_url

from . import engine as engine_mod
from .errors import ApiError, safe_text, storage_unavailable, validation
from .runs import RUN_ID, RunManager
from .scenarios import RunScenarios
from .settings import API_VERSION, PROFILES, Settings
from .storage import Storage
from .views import PREVIEW_NAME, build_evidence, default_label, observation_display_url

log = logging.getLogger("methadata_api")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:\-]{0,127}$")
ALLOWED_UPLOAD = {"zip", "har", "json", "html", "mhtml", "png", "jpeg", "webp", "gif", "mp4", "mp3"}
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._\-]")
MAX_JSON_BYTES = 1 << 20
MAX_ARTIFACTS_LISTED = 200
URL_NOTE = "Adres nie jest pobierany. Analiza obejmie tylko sam URL, więc część porównań może być niewykonalna."
FILE_NOTE = "Plik przetworzony lokalnie, bez pobierania zasobów z sieci."


def _json(status: int, body: Any, headers: Optional[dict] = None) -> JSONResponse:
    return JSONResponse(body, status_code=status, headers=headers)


async def _read_json_body(request: Request) -> Any:
    ct = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if ct != "application/json":
        raise ApiError(415, "unsupported_media_type", "Ten typ treści nie jest obsługiwany. Wyślij dane jako application/json.",
                       details=[{"field": "Content-Type", "message": f"Nieobsługiwany typ: {safe_text(ct or 'brak', 100)}."}])
    chunks, size = [], 0
    async for c in request.stream():
        size += len(c)
        if size > MAX_JSON_BYTES:
            raise ApiError(413, "payload_too_large", "Żądanie jest za duże.")
        chunks.append(c)
    import json
    try:
        return json.loads(b"".join(chunks))
    except ValueError:
        raise ApiError(400, "bad_request", "Nie udało się odczytać żądania. Sprawdź, czy treść jest poprawnym JSON-em.")


def _validate_url(url: Any) -> str:
    msg = "Podany adres jest niepoprawny. Wpisz pełny adres zaczynający się od http:// lub https://."
    if not isinstance(url, str) or not url.strip():
        raise validation(msg, "url", "Podaj adres.")
    url = url.strip()
    if len(url) > 2048:
        raise validation(msg, "url", "Adres jest za długi (limit 2048 znaków).")
    try:
        parts = urlsplit(url)
        host = parts.hostname
        parts.port  # noqa: B018 - waliduje numer portu
    except ValueError:
        raise validation(msg, "url", "Adres jest niepoprawny.")
    if parts.scheme.lower() not in ("http", "https") or not re.match(r"^https?://\S+$", url, re.I):
        raise validation(msg, "url", "Adres musi zaczynać się od http:// lub https://.")
    if not host:
        raise validation(msg, "url", "Adres nie zawiera nazwy serwera.")
    return url


def _observation_body(obs: Observation, created: bool, label: Optional[str]) -> dict:
    arts = obs.artifacts
    listed = [{"artifact_id": a.artifact_id, "role": a.role.value, "media_type": a.media_type, "size": a.size,
               "sha256": a.sha256, "display_url": a.display_url} for a in arts[:MAX_ARTIFACTS_LISTED]]
    offline_url = obs.input_kind is InputKind.url_only
    note = URL_NOTE if offline_url else FILE_NOTE
    if len(arts) > MAX_ARTIFACTS_LISTED:
        note += f" Pokazano pierwsze {MAX_ARTIFACTS_LISTED} z {len(arts)} plików."
    return {"observation_id": obs.observation_id, "canonical_id": obs.canonical_id, "input_kind": obs.input_kind.value,
            "created": created, "label": safe_text(label or default_label(obs), 300),
            "display_url": observation_display_url(obs), "artifacts": listed,
            "analysis": {"mode": "offline", "note": note}}


def create_app(settings: Optional[Settings] = None, *, engine=None) -> FastAPI:
    settings = settings or Settings.from_env()
    storage = Storage(settings)
    manager = RunManager(storage, engine or engine_mod.compute_run)
    scenarios = RunScenarios(manager)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            storage.prepare()
            storage.seed_demo()
            n = manager.recover()
            if n:
                log.warning("oznaczono %d przerwanych przebiegów jako failed", n)
        except Exception:  # noqa: BLE001 - backend startuje, a health i błędy 503 pokażą problem z magazynem
            log.exception("inicjalizacja magazynu nie powiodła się")
        yield
        manager.shutdown(wait=True)

    app = FastAPI(title="Methadata API", version=API_VERSION, lifespan=lifespan, docs_url=None, redoc_url=None,
                  openapi_url=None)
    app.state.settings, app.state.storage, app.state.runs = settings, storage, manager

    # ------------------------------------------------------------ błędy
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, e: ApiError):
        return _json(e.status, e.body())

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, e: StarletteHTTPException):
        if e.status_code == 404:
            return _json(404, ApiError(404, "bad_request", "Nie ma takiego adresu API.").body())
        if e.status_code == 405:
            return _json(405, ApiError(405, "bad_request", "Ta metoda nie jest obsługiwana dla tego adresu.").body())
        return _json(e.status_code, ApiError(e.status_code, "bad_request", "Nie udało się obsłużyć żądania.").body())

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, e: Exception):
        log.exception("nieoczekiwany błąd")
        return _json(500, ApiError(500, "internal_error", "Wystąpił nieoczekiwany błąd. Spróbuj ponownie.", retryable=True).body())

    @app.middleware("http")
    async def _headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        return resp

    def need_storage() -> None:
        if not storage.available():
            raise storage_unavailable()

    def analysis_storage(profile: str) -> Storage:
        selected = storage.for_profile(profile)
        if selected is None:
            raise validation("Profil analizy jest niedostępny.", "profile", "Uruchom usługę z przygotowanym korpusem jury.")
        if profile == "jury" and (not selected.available() or not selected.index()):
            raise storage_unavailable()
        return selected

    # ------------------------------------------------------------ system
    @app.get("/api/v1/health")
    def health():
        ok = storage.available()
        body = {"status": "ok" if ok else "unavailable", "api": "ok", "storage": "ok" if ok else "unavailable",
                "version": API_VERSION, "schema_version": "1"}
        return _json(200 if ok else 503, body)

    @app.get("/api/v1/demo")
    def demo():
        need_storage()
        try:
            info = storage.seed_demo()
            store = storage.store()
            canon = {store.observation(i).canonical_id for i in [*info["observation_ids"], info["target_observation_id"]]}
        except Exception:  # noqa: BLE001
            log.exception("demo")
            raise storage_unavailable()
        return _json(200, {"observation_id": info["target_observation_id"], "dataset_name": info["dataset_name"],
                           "dataset_version": info["dataset_version"], "description": info["description"],
                           "material_count": len(canon), "input_kind": InputKind.full_capture.value})

    # ------------------------------------------------------------ obserwacje
    @app.post("/api/v1/observations")
    async def create_observation(request: Request):
        need_storage()
        ct = request.headers.get("content-type", "").split(";")[0].strip().lower()
        if ct == "multipart/form-data":
            return await _observation_from_file(request)
        body = await _read_json_body(request)
        if not isinstance(body, dict):
            raise ApiError(400, "bad_request", "Nie udało się odczytać żądania. Oczekiwano obiektu JSON.")
        unknown = sorted(set(body) - {"url", "mode", "label"})
        if unknown:
            raise validation("Żądanie zawiera nieznane pola.", unknown[0], "Nieznane pole.")
        if body.get("mode", "offline") != "offline":
            raise validation("Obsługiwany jest tylko tryb offline.", "mode", "Dozwolona wartość: offline.")
        label = body.get("label")
        if label is not None and (not isinstance(label, str) or len(label) > 200):
            raise validation("Etykieta jest niepoprawna.", "label", "Tekst do 200 znaków.")
        url = _validate_url(body.get("url"))

        selected = analysis_storage(settings.default_profile)

        def work():
            with selected.lock:
                rep = submit_url(url, selected.store())
                if not rep.entries:
                    raise validation("Podany adres jest niepoprawny.", "url", "Adres nie został przyjęty.")
                e = rep.entries[0]
                if label:
                    selected.set_label(e.observation_id, label)
                return selected.store().observation(e.observation_id), e.status != "unchanged"

        obs, created = await run_in_threadpool(work)
        return _created(obs, created, label or selected.labels().get(obs.observation_id))

    def _created(obs: Observation, created: bool, label: Optional[str]) -> JSONResponse:
        return _json(201 if created else 200, _observation_body(obs, created, label),
                     {"Location": f"/api/v1/observations/{obs.observation_id}"} if created else None)

    async def _observation_from_file(request: Request):
        selected = analysis_storage(settings.default_profile)
        limit = settings.max_upload_bytes
        cap = limit + (1 << 20)  # nagłówki i pola multipart
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > cap:
            raise _too_large()
        seen = 0
        orig_receive = request.receive

        async def limited():
            nonlocal seen
            msg = await orig_receive()
            if msg["type"] == "http.request":
                seen += len(msg.get("body", b""))
                if seen > cap:
                    raise _too_large()
            return msg

        try:
            form = await Request(request.scope, limited).form()
        except ApiError:
            raise
        except Exception:  # noqa: BLE001
            raise ApiError(400, "bad_request", "Nie udało się odczytać przesłanego formularza.")
        try:
            up = form.get("file")
            if not isinstance(up, UploadFile):
                raise validation("Brak pliku w żądaniu.", "file", "Dodaj plik w polu file.")
            label = form.get("label")
            if label is not None and (not isinstance(label, str) or len(label) > 200):
                raise validation("Etykieta jest niepoprawna.", "label", "Tekst do 200 znaków.")
            name = _SAFE_NAME.sub("_", Path(up.filename or "").name) or "upload"
            tmp = tempfile.TemporaryDirectory(prefix="upload-")
            try:
                path = Path(tmp.name) / name
                size, head = 0, b""
                with path.open("wb") as out:
                    while chunk := await up.read(1 << 20):
                        size += len(chunk)
                        if size > limit:
                            raise _too_large()
                        if len(head) < 65536:
                            head += chunk[: 65536 - len(head)]
                        out.write(chunk)
                if size == 0:
                    raise validation("Plik jest pusty.", "file", "Dodaj niepusty plik.")
                kind = sniff(head, name, size).kind
                if kind not in ALLOWED_UPLOAD:
                    raise ApiError(415, "unsupported_media_type",
                                   "Ten typ pliku nie jest obsługiwany. Dodaj ZIP, HAR, HTML, obraz, film lub plik audio.",
                                   details=[{"field": "file", "message": f"Nieobsługiwany typ: {sniff(head, name, size).media_type}."}])

                def work():
                    with selected.lock:
                        store = selected.store()
                        rep = submit_files([path], store)
                        if not rep.entries:
                            why = "; ".join(i.message for i in rep.issues[:3]) or "plik nie zawiera przyjmowalnych danych"
                            raise validation("Nie udało się przyjąć pliku.", "file", why)
                        e = rep.entries[0]
                        if label:
                            selected.set_label(e.observation_id, label)
                        return store.observation(e.observation_id), e.status != "unchanged"

                obs, created = await run_in_threadpool(work)
            finally:
                tmp.cleanup()
        finally:
            await form.close()
        return _created(obs, created, label or selected.labels().get(obs.observation_id))

    def _too_large() -> ApiError:
        return ApiError(413, "payload_too_large", "Plik jest za duży. Maksymalny rozmiar to 50 MiB.",
                        details=[{"field": "file", "message": f"Przekroczono limit {settings.max_upload_bytes} bajtów."}])

    # ------------------------------------------------------------ przebiegi
    @app.post("/api/v1/runs")
    async def create_run(request: Request):
        need_storage()
        body = await _read_json_body(request)
        if not isinstance(body, dict):
            raise ApiError(400, "bad_request", "Nie udało się odczytać żądania. Oczekiwano obiektu JSON.")
        unknown = sorted(set(body) - {"observation_id", "profile"})
        if unknown:
            raise validation("Żądanie zawiera nieznane pola.", unknown[0], "Nieznane pole.")
        oid = body.get("observation_id")
        if not isinstance(oid, str) or not ID_RE.match(oid):
            raise validation("Podaj poprawny identyfikator materiału.", "observation_id", "Brak lub niepoprawne ID.")
        profile = body.get("profile", settings.default_profile)
        if profile not in PROFILES:
            raise validation("Nieznany profil analizy.", "profile", f"Dostępne profile: {', '.join(PROFILES)}.")
        selected = analysis_storage(profile)
        try:
            known = bool(selected.store().versions(oid))
        except OSError:
            raise storage_unavailable()
        if not known:
            raise ApiError(404, "observation_not_found", "Nie znaleziono takiego materiału. Dodaj go ponownie.",
                           details=[{"field": "observation_id", "message": "Nieznane ID."}])
        st = await run_in_threadpool(manager.create, oid, profile)
        loc = f"/api/v1/runs/{st['run_id']}"
        return _json(202, {"run_id": st["run_id"], "observation_id": oid, "status": "queued", "status_url": loc},
                     {"Location": loc})

    def _run_state(run_id: str) -> dict:
        need_storage()
        st = manager.state(run_id) if RUN_ID.match(run_id) else None
        if st is None:
            raise ApiError(404, "run_not_found", "Nie znaleziono takiej analizy. Uruchom analizę ponownie.")
        return st

    @app.get("/api/v1/runs/{run_id}")
    def get_run(run_id: str):
        return _json(200, manager.public(_run_state(run_id)))

    @app.get("/api/v1/runs/{run_id}/evidence/{evidence_id}")
    def get_evidence(run_id: str, evidence_id: str):
        st = _run_state(run_id)
        if st["status"] != "succeeded":
            raise ApiError(409, "run_not_ready", "Analiza jeszcze się nie zakończyła, dowody są niedostępne. Spróbuj za chwilę.",
                           retryable=st["status"] in ("queued", "running"))
        bundle, view = manager.bundle(run_id), manager.result(run_id)
        ev = None
        selected = storage.for_profile(st.get("profile", "demo"))
        if selected is not None and bundle is not None and view is not None and ID_RE.match(evidence_id):
            ev = build_evidence(run_id, bundle, view, evidence_id, selected.store(), settings.previews_dir)
        if ev is None:
            raise ApiError(404, "evidence_not_found", "Nie znaleziono takiego dowodu w tej analizie.")
        return _json(200, ev)

    @app.get("/api/v1/runs/{run_id}/evidence/{evidence_id}/scenarios")
    def get_scenario(run_id: str, evidence_id: str, request: Request):
        st = _run_state(run_id)
        if st["status"] != "succeeded":
            raise ApiError(409, "run_not_ready", "Analiza jeszcze się nie zakończyła. Spróbuj za chwilę.",
                           retryable=st["status"] in ("queued", "running"))
        query = request.query_params
        return _json(200, scenarios.compare(st, evidence_id, query.getlist("disabled"), query.get("material_id")))

    # ------------------------------------------------------------ statyczne podglądy
    @app.get("/static/previews/{name}")
    def preview(name: str):
        p = settings.previews_dir / name
        if not PREVIEW_NAME.match(name) or not p.is_file():
            raise ApiError(404, "bad_request", "Nie znaleziono podglądu.")
        return FileResponse(p, headers={"Cache-Control": "public, max-age=31536000, immutable"})

    return app
