"""Okresowy import z kursorem: strona rekordów -> obserwacje (dedup/aktualizacja) -> kursor -> analiza nowych/zmienionych.

Gwarancje:
  * kursor przesuwa się dopiero po zapisie CAŁEJ strony w magazynie (przerwanie = ponowienie tej samej strony,
    zapis jest idempotentny: identyczny rekord = `unchanged`, zmieniony = `new_version`),
  * nowe/zmienione obserwacje trafiają do kolejki `pending` w tym samym zapisie stanu co kursor; wyczyszczenie
    kolejki następuje dopiero po udanej analizie (błąd analizy nie gubi rekordów, następny przebieg ponawia),
  * błąd przejściowy dostawcy jest ponawiany z odstępem, błąd stały kończy przebieg bez ruszania kursora.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Sequence

from ..contracts import ids
from ..contracts.models import Artifact, ArtifactRole, InputKind, Observation
from ..ingest import Store
from ..ingest.store import now_iso
from .provider import Page, ProviderError, SourceProvider
from .state import LockedError, MonitorState, StateFile

TAG = "ad_library"
Analyzer = Callable[[Store, Sequence[str]], dict]


@dataclass
class PageResult:
    new: int = 0
    updated: int = 0
    duplicate: int = 0
    rejected: int = 0
    changed_ids: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)


@dataclass
class RunReport:
    source: str
    pages: int = 0
    new: int = 0
    updated: int = 0
    duplicate: int = 0
    rejected: int = 0
    cursor: Optional[str] = None
    exhausted: bool = False
    analyzed: int = 0
    analysis: Optional[dict] = None
    error: Optional[str] = None
    retries: int = 0
    issues: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.error is None

    def summary(self) -> dict:
        return {k: getattr(self, k) for k in ("source", "pages", "new", "updated", "duplicate", "rejected", "cursor",
                                              "exhausted", "analyzed", "analysis", "error", "retries", "issues")}


def record_id(rec: dict) -> Optional[str]:
    v = rec.get("id") or rec.get("ad_archive_id")
    return str(v).strip() if v not in (None, "") and str(v).strip() else None


def build_observation(store: Store, source_id: str, rec: dict, fetched_at: str,
                      prev: Optional[Observation]) -> Observation:
    rid = record_id(rec)
    sref = f"{source_id}:{rid}"
    oid = ids.observation_id(sref, InputKind.creative_only.value)
    data = json.dumps(rec, ensure_ascii=False, sort_keys=True, indent=1).encode("utf-8")
    sha, size, _ = store.put_bytes(data)
    art = Artifact(artifact_id=ids.artifact_id(oid, ArtifactRole.document.value, sha, "ad-record.json"),
                   observation_id=oid, role=ArtifactRole.document, local_ref=store.blob_ref(sha), sha256=sha,
                   size=size, media_type="application/json", origin="ad-record.json")
    # Ponowne pobranie tej samej treści nie tworzy nowej wersji (czas przejmujemy z poprzedniej).
    ctime = prev.capture_time if prev and {a.artifact_id for a in prev.artifacts} == {art.artifact_id} else fetched_at
    return Observation(observation_id=oid, canonical_id=ids.canonical_id([], url=sref), source_ref=sref,
                       input_kind=InputKind.creative_only, artifacts=[art], capture_time=ctime,
                       tags=sorted([TAG, f"source:{source_id}"]))


def ingest_page(store: Store, source_id: str, page: Page, import_id: str) -> PageResult:
    res = PageResult()
    fetched = page.fetched_at or now_iso()
    seen: dict[str, str] = {}
    for rec in page.records:
        rid = record_id(rec)
        if rid is None:
            res.rejected += 1
            res.issues.append("rekord bez id")
            continue
        try:
            oid = ids.observation_id(f"{source_id}:{rid}", InputKind.creative_only.value)
            vs = store.versions(oid)
            prev = Observation.model_validate(vs[-1]["observation"]) if vs else None
            obs = build_observation(store, source_id, rec, fetched, prev)
            status, _ = store.save_observation(obs, import_id)
        except Exception as e:  # rekord niezapisywalny nie blokuje reszty, ale zatrzyma kursor (patrz run_once)
            raise RecordWriteError(f"zapis rekordu {rid}: {type(e).__name__}: {e}") from e
        if status == "unchanged":
            res.duplicate += 1
        else:
            res.new += status == "new"
            res.updated += status == "new_version"
            if oid not in seen:
                res.changed_ids.append(oid)
        seen[oid] = status
    return res


class RecordWriteError(Exception):
    pass


def run_once(provider: SourceProvider, store: Store | str, *, analyzer: Optional[Analyzer] = None, page_size: int = 100,
             max_pages: int = 10, retries: int = 2, backoff_s: float = 2.0, sleep: Callable[[float], None] = time.sleep,
             name: Optional[str] = None) -> RunReport:
    """Jeden przebieg (do wywołania z crona/launchd). Zwraca raport; błędy nie są rzucane, lecz zapisane w `error`."""
    store = store if isinstance(store, Store) else Store(store)
    name = name or provider.source_id
    sf = StateFile(store.root, name)
    rep = RunReport(source=name)
    try:
        with sf.lock():
            _run_locked(provider, store, sf, rep, analyzer, page_size, max_pages, retries, backoff_s, sleep)
    except LockedError as e:
        rep.error = str(e)
    return rep


def _run_locked(provider, store, sf, rep, analyzer, page_size, max_pages, retries, backoff_s, sleep) -> None:
    st = sf.load()
    started = now_iso()
    import_id = "monitor-" + ids.sha256_bytes(f"{started}|{st.cursor}".encode())[:8]
    for _ in range(max_pages):
        page = _fetch_with_retry(provider, st.cursor, page_size, rep, retries, backoff_s, sleep)
        if page is None:
            break
        try:
            pr = ingest_page(store, provider.source_id, page, import_id)
        except RecordWriteError as e:
            rep.error = str(e)
            break
        rep.pages += 1
        rep.new += pr.new
        rep.updated += pr.updated
        rep.duplicate += pr.duplicate
        rep.rejected += pr.rejected
        rep.issues.extend(pr.issues)
        # Kursor i kolejka analizy w jednym atomowym zapisie, dopiero po zapisie całej strony.
        st.pending = sorted(set(st.pending) | set(pr.changed_ids))
        if page.next_cursor is not None:
            st.cursor = page.next_cursor
        elif page.end_reset:
            st.cursor = None
        elif page.end_cursor is not None:
            st.cursor = page.end_cursor
        for k, v in (("new", pr.new), ("updated", pr.updated), ("duplicate", pr.duplicate), ("rejected", pr.rejected)):
            st.totals[k] = st.totals.get(k, 0) + v
        st.last_run = started
        sf.save(st)
        if page.next_cursor is None:
            rep.exhausted = True
            break
    if st.pending and analyzer is not None:
        try:
            rep.analysis = analyzer(store, list(st.pending))
            rep.analyzed = len(st.pending)
            st.pending = []
        except Exception as e:
            rep.error = rep.error or f"analiza: {type(e).__name__}: {e}"[:300]
    st.last_error = rep.error
    st.last_run = started
    rep.cursor = st.cursor
    sf.save(st)
    sf.log({"at": started, **rep.summary()})


def _fetch_with_retry(provider, cursor, limit, rep, retries, backoff_s, sleep) -> Optional[Page]:
    for attempt in range(retries + 1):
        try:
            return provider.fetch(cursor, limit)
        except ProviderError as e:
            if not e.transient or attempt == retries:
                rep.error = f"dostawca: {e}"[:300]
                return None
            rep.retries += 1
            sleep(backoff_s * (2 ** attempt))
        except Exception as e:  # nieoczekiwany błąd dostawcy traktujemy jak stały
            rep.error = f"dostawca: {type(e).__name__}"[:300]
            return None
    return None
