"""Interfejs dostawcy rekordów reklam (S01: Meta Ad Library lub jej eksport) dla okresowego importu.

Dostawca zwraca strony rekordów z kursorem nieprzezroczystym dla monitora. Dwie implementacje:
  * JsonFileProvider  – eksport z dysku (JSON/JSONL, plik lub katalog), bez sieci,
  * MetaAdLibraryProvider – Graph API `ads_archive` (wymaga tokenu w zmiennej środowiskowej; sieć tylko tu).
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Protocol, Sequence, runtime_checkable


class ProviderError(Exception):
    """Błąd dostawcy; `transient` = warto ponowić (sieć, 5xx, limit)."""

    def __init__(self, msg: str, transient: bool = True):
        super().__init__(msg)
        self.transient = transient


@dataclass
class Page:
    records: list[dict]
    next_cursor: Optional[str] = None   # None = koniec danych (kursor zostaje na ostatniej wartości)
    fetched_at: Optional[str] = None    # ISO-8601 UTC; czas pobrania strony
    end_cursor: Optional[str] = None    # po wyczerpaniu: kursor do wznowienia (np. koniec eksportu)
    end_reset: bool = False             # po wyczerpaniu: wróć na początek (źródło sortowane od najnowszych)


@runtime_checkable
class SourceProvider(Protocol):
    source_id: str                      # stabilny identyfikator repozytorium, np. "meta-ad-library"

    def fetch(self, cursor: Optional[str], limit: int) -> Page: ...


def _records_of(doc: Any) -> list[dict]:
    if isinstance(doc, dict) and isinstance(doc.get("data"), list):
        return [r for r in doc["data"] if isinstance(r, dict)]
    if isinstance(doc, list):
        return [r for r in doc if isinstance(r, dict)]
    return [doc] if isinstance(doc, dict) else []


class JsonFileProvider:
    """Eksport z dysku. Kursor = liczba już przekazanych rekordów (rekordy w kolejności plików, potem w pliku);
    eksport ma być dopisywany na końcu. Nowszy eksport tej samej reklamy = aktualizacja po `id`."""

    def __init__(self, path: str | Path, source_id: str = "meta-ad-library",
                 clock: Optional[Callable[[], str]] = None):
        self.path = Path(path)
        self.source_id = source_id
        self._clock = clock

    def _files(self) -> list[Path]:
        if self.path.is_dir():
            return sorted(p for p in self.path.iterdir() if p.suffix in (".json", ".jsonl") and p.is_file())
        return [self.path]

    def _all(self) -> list[dict]:
        out: list[dict] = []
        for p in self._files():
            try:
                text = p.read_text(encoding="utf-8")
            except OSError as e:
                raise ProviderError(f"nie można odczytać {p.name}: {e}", transient=False)
            try:
                if p.suffix == ".jsonl":
                    for line in text.splitlines():
                        if line.strip():
                            out.extend(_records_of(json.loads(line)))
                else:
                    out.extend(_records_of(json.loads(text)))
            except ValueError as e:
                raise ProviderError(f"niepoprawny JSON w {p.name}: {e}", transient=False)
        return out

    def fetch(self, cursor: Optional[str], limit: int) -> Page:
        from ..ingest.store import now_iso
        start = int(cursor or 0)
        allr = self._all()
        chunk = allr[start:start + limit]
        end = start + len(chunk)
        return Page(chunk, str(end) if end < len(allr) else None, (self._clock or now_iso)(), end_cursor=str(end))


class MetaAdLibraryProvider:
    """Graph API Meta Ad Library (`ads_archive`). Wymaga tokenu dostępu w zmiennej środowiskowej
    (domyślnie META_AD_LIBRARY_TOKEN) i potwierdzonej tożsamości dewelopera Meta. Kursor = `paging.cursors.after`."""

    source_id = "meta-ad-library"
    FIELDS = ("id,page_id,page_name,ad_creation_time,ad_delivery_start_time,ad_delivery_stop_time,"
              "ad_creative_bodies,ad_creative_link_titles,ad_snapshot_url,bylines,funding_entity,"
              "beneficiary_payers,languages")

    def __init__(self, search_terms: str = "", countries: Sequence[str] = ("PL",), token_env: str = "META_AD_LIBRARY_TOKEN",
                 api_version: str = "v21.0", timeout_s: float = 20.0,
                 opener: Optional[Callable[[str, float], bytes]] = None, clock: Optional[Callable[[], str]] = None):
        self.search_terms, self.countries = search_terms, list(countries)
        self.token_env, self.api_version, self.timeout_s = token_env, api_version, timeout_s
        self._opener, self._clock = opener or self._http_get, clock

    @staticmethod
    def _http_get(url: str, timeout: float) -> bytes:
        import urllib.error
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310 (stały host Graph API)
                return r.read(32 * 1024 * 1024)
        except urllib.error.HTTPError as e:
            raise ProviderError(f"HTTP {e.code}", transient=e.code >= 500 or e.code == 429)
        except (urllib.error.URLError, OSError) as e:
            raise ProviderError(f"sieć: {type(e).__name__}", transient=True)  # bez URL: zawiera token

    def fetch(self, cursor: Optional[str], limit: int) -> Page:
        from ..ingest.store import now_iso
        token = os.environ.get(self.token_env)
        if not token:
            raise ProviderError(f"brak tokenu w zmiennej {self.token_env}", transient=False)
        q = {"access_token": token, "search_terms": self.search_terms, "ad_type": "ALL", "fields": self.FIELDS,
             "ad_reached_countries": json.dumps(self.countries), "limit": str(limit)}
        if cursor:
            q["after"] = cursor
        url = f"https://graph.facebook.com/{self.api_version}/ads_archive?" + urllib.parse.urlencode(q)
        try:
            doc = json.loads(self._opener(url, self.timeout_s))
        except ValueError:
            raise ProviderError("odpowiedź nie jest JSON", transient=True)
        if isinstance(doc, dict) and doc.get("error"):
            e = doc["error"]
            raise ProviderError(f"API: {e.get('message', 'błąd')}"[:200], transient=e.get("is_transient", False))
        nxt = ((doc.get("paging") or {}).get("cursors") or {}).get("after") if (doc.get("paging") or {}).get("next") else None
        return Page(_records_of(doc), nxt, (self._clock or now_iso)(), end_reset=True)
