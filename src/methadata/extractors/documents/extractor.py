"""Ekstraktor X04: PDF, JSON, TXT, czas/tytuł pobrania, manifesty sum, atrybuty cookies i klucze Web Storage.

Rozdział wartości: oryginał zostaje w artefakcie (bajty). Cechy są zredagowane: cookies -> tylko nazwy i atrybuty
(bez wartości, także bez ich skrótów), Web Storage -> tylko nazwy kluczy, adres pobrania -> `mask_url`
(+ skrót surowego adresu). Dowody: `artifact_id` + lokalizacja (TextSpan dla TXT, ByteRange dla JSON,
całość artefaktu dla PDF/stron).
"""
from __future__ import annotations

import io
import json
import re
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Protocol

from methadata.contracts import ids
from methadata.contracts.interfaces import AdapterKind, AdapterSpec, ArtifactReader
from methadata.contracts.models import (Artifact, ArtifactRef, ArtifactRole, ByteRange, ExtractionState,
                                        ExtractionStatus, Feature, Observation, ParentRelation, TextSpan, ValueType)
from methadata.contracts.urls import mask_url

VERSION = "1.0.0"
EXTRACTOR_VERSION = f"x04-documents/{VERSION}"
ROLES = {ArtifactRole.document, ArtifactRole.other, ArtifactRole.resource}
STORAGE_FILES = {"cookies.json": "cookies", "localstorage.json": "localStorage", "sessionstorage.json": "sessionStorage"}
_TS_TXT = re.compile(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})(?:\.\d+)?\s*(UTC|Z)\b")
_HEX64 = re.compile(r"\b[0-9a-fA-F]{64}\b")


class DerivedStore(Protocol):
    def put_derived(self, artifact: Artifact, data: bytes) -> None: ...


def _base(a: Artifact) -> str:
    return (a.origin or a.local_ref or "").replace("\\", "/").rsplit("/", 1)[-1]


def _iso_utc(s: str) -> Optional[str]:
    s = s.strip()
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00").replace(" UTC", ""))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + (f".{d.microsecond:06d}" if d.microsecond else "") + "Z"


def _pdf_date(s: str) -> Optional[str]:
    m = re.match(r"D:(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?([Zz+-])?(\d{2})?'?(\d{2})?", s or "")
    if not m:
        return None
    y, mo, d, h, mi, se = (int(x) if x else dflt for x, dflt in zip(m.groups()[:6], (0, 1, 1, 0, 0, 0)))
    try:
        dt = datetime(y, mo, d, h, mi, se, tzinfo=timezone.utc)
    except ValueError:
        return None
    sign, oh, om = m.group(7), m.group(8), m.group(9)
    if sign in ("+", "-") and oh:
        from datetime import timedelta
        off = timedelta(hours=int(oh), minutes=int(om or 0))
        dt = dt - off if sign == "+" else dt + off
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class DocumentsExtractor:
    spec = AdapterSpec(id="extract-documents", kind=AdapterKind.extractor, version=VERSION,
                       description="PDF/JSON/TXT, czas i tytuł pobrania, manifesty sum, atrybuty cookies, klucze Web Storage")

    def __init__(self, store: Optional[DerivedStore] = None):
        self.store = store

    def extract(self, observation: Observation, *, reader: ArtifactReader,
                optional: Mapping[str, Any] | None = None):
        feats: list[Feature] = []
        sts: list[ExtractionStatus] = []
        oid = observation.observation_id
        cands = [a for a in observation.artifacts if a.role in ROLES]
        handled = 0
        for art in cands:
            kind = self._kind(art)
            if kind is None:
                continue
            handled += 1
            try:
                raw = reader.open_bytes(ArtifactRef(artifact_id=art.artifact_id))
            except Exception as e:
                sts.append(self._st(oid, art, ExtractionState.missing_input, f"nie można odczytać bajtów: {e}"))
                continue
            if kind == "json_or_pdf_sniff":
                kind = "pdf" if raw.lstrip()[:5] == b"%PDF-" else "json"
            try:
                f, s = getattr(self, "_" + kind)(observation, art, raw, reader)
                feats += f
                sts += s
            except Exception as e:
                sts.append(self._st(oid, art, ExtractionState.failed, f"{type(e).__name__}: {e}"))
        if not handled:
            sts.append(self._st(oid, None, ExtractionState.missing_input,
                                "brak artefaktu dokumentu (PDF/JSON/TXT/manifest/storage)"))
        return feats, sts

    # ------------------------------------------------------------ pomocnicze
    @staticmethod
    def _st(oid, art, state, detail):
        return ExtractionStatus(observation_id=oid, artifact_id=art.artifact_id if art else None,
                                extractor_version=EXTRACTOR_VERSION, state=state, detail=detail)

    @staticmethod
    def _kind(a: Artifact) -> Optional[str]:
        n = _base(a).lower()
        mt = (a.media_type or "").lower()
        if n in STORAGE_FILES:
            return "storage"
        if n == "archive_info.json":
            return "download_info"
        if n == "sha256.json" or n.endswith(".sha256"):
            return "manifest"
        if n.endswith(".pdf") or mt == "application/pdf":
            return "pdf"
        if n.endswith(".json") or mt == "application/json":
            return "json_or_pdf_sniff"
        if n.endswith(".txt") or mt == "text/plain":
            return "txt"
        return None

    def _mk(self, obs, art, family):
        feats: list[Feature] = []
        seen: set[str] = set()

        def add(kind, vt, value, refs):
            fid = ids.feature_id(obs.observation_id, family, kind, EXTRACTOR_VERSION, [art.artifact_id, value])
            if fid in seen:
                return
            seen.add(fid)
            feats.append(Feature(feature_id=fid, observation_id=obs.observation_id, family=family, kind=kind,
                                 value_type=vt, value=value, extractor_version=EXTRACTOR_VERSION,
                                 evidence_refs=list(refs)))
        return feats, add

    @staticmethod
    def _whole(art):
        return [ArtifactRef(artifact_id=art.artifact_id)]

    @staticmethod
    def _key_ref(art, raw: bytes, key: str):
        needle = json.dumps(key, ensure_ascii=False).encode("utf-8")
        i = raw.find(needle)
        if i < 0:
            needle = json.dumps(key).encode("ascii")
            i = raw.find(needle)
        if i < 0:
            return [ArtifactRef(artifact_id=art.artifact_id)]
        return [ArtifactRef(artifact_id=art.artifact_id, locator=ByteRange(start=i, end=i + len(needle)))]

    @staticmethod
    def _decode(raw: bytes) -> str:
        if raw.startswith(b"\xef\xbb\xbf"):
            raw = raw[3:]
        return raw.decode("utf-8", "replace")

    # ------------------------------------------------------------ TXT
    def _txt(self, obs, art, raw, reader):
        feats, add = self._mk(obs, art, "document")
        text = self._decode(raw)
        norm = "\n".join(l.rstrip() for l in text.splitlines()).strip()
        add("txt_text", ValueType.string, norm[:20000],
            [ArtifactRef(artifact_id=art.artifact_id, locator=TextSpan(start=0, end=len(text)))] if text else self._whole(art))
        add("txt_sha256", ValueType.string, ids.sha256_bytes(norm.encode("utf-8")), self._whole(art))
        add("txt_stats", ValueType.json, {"chars": len(text), "lines": len(text.splitlines())}, self._whole(art))
        m = _TS_TXT.search(text)
        if m:
            iso = _iso_utc(f"{m.group(1)}T{m.group(2)}+00:00")
            add("download_time", ValueType.json, {"time": iso, "source": "page_text", "precision": "second"},
                [ArtifactRef(artifact_id=art.artifact_id, locator=TextSpan(start=m.start(), end=m.end()))])
        return feats, [self._st(obs.observation_id, art, ExtractionState.ok, f"txt; znaków={len(text)}")]

    # ------------------------------------------------------------ PDF
    def _pdf(self, obs, art, raw, reader):
        try:
            from pypdf import PdfReader
        except ImportError:
            return [], [self._st(obs.observation_id, art, ExtractionState.unsupported, "brak biblioteki pypdf (extra 'pdf')")]
        if raw.lstrip()[:5] != b"%PDF-":
            return [], [self._st(obs.observation_id, art, ExtractionState.failed, "uszkodzony PDF: brak nagłówka %PDF-")]
        try:
            pdf = PdfReader(io.BytesIO(raw))
            npages = len(pdf.pages)
            if pdf.is_encrypted:
                return [], [self._st(obs.observation_id, art, ExtractionState.unsupported, "PDF zaszyfrowany")]
            meta = pdf.metadata
            texts = []
            for p in pdf.pages:
                try:
                    texts.append(p.extract_text() or "")
                except Exception:
                    texts.append("")
        except Exception as e:
            return [], [self._st(obs.observation_id, art, ExtractionState.failed, f"uszkodzony PDF: {type(e).__name__}: {e}")]
        feats, add = self._mk(obs, art, "document")
        whole = self._whole(art)
        add("pdf_info", ValueType.json, {"pages": npages, "version": raw.lstrip()[:8].decode("ascii", "ignore").strip()}, whole)
        if meta:
            for k, kind in (("/Title", "pdf_title"), ("/Producer", "pdf_producer"), ("/Creator", "pdf_creator"),
                            ("/Author", "pdf_author")):
                v = meta.get(k)
                if v:
                    add(kind, ValueType.string, str(v), whole)
            for k, kind in (("/CreationDate", "pdf_created"), ("/ModDate", "pdf_modified")):
                v = meta.get(k)
                if v:
                    add(kind, ValueType.json, {"raw": str(v), "time": _pdf_date(str(v))}, whole)
        full = "\n".join(t.strip() for t in texts).strip()
        for i, t in enumerate(texts, 1):
            if t.strip():
                add("pdf_page_text", ValueType.json, {"page": i, "text": t.strip()[:5000]}, whole)
        add("pdf_text_sha256", ValueType.string, ids.sha256_bytes(full.encode("utf-8")), whole)
        add("pdf_has_text", ValueType.boolean, bool(full), whole)
        if self.store is not None and full:
            data = full.encode("utf-8")
            sha = ids.sha256_bytes(data)
            d = Artifact(artifact_id=ids.artifact_id(obs.observation_id, "document", sha, f"{art.artifact_id}|pdf-text"),
                         observation_id=obs.observation_id, role=ArtifactRole.document,
                         local_ref=f"derived/{sha[:2]}/{sha}", sha256=sha, size=len(data), media_type="text/plain",
                         parent=ArtifactRef(artifact_id=art.artifact_id), parent_relation=ParentRelation.derived_from,
                         origin="pdf-text")
            self.store.put_derived(d, data)
        return feats, [self._st(obs.observation_id, art, ExtractionState.ok, f"pdf; stron={npages}; znaków={len(full)}")]

    # ------------------------------------------------------------ JSON ogólny
    def _load_json(self, raw):
        return json.loads(self._decode(raw))

    def _json(self, obs, art, raw, reader):
        data = self._load_json(raw)
        feats, add = self._mk(obs, art, "document")
        whole = self._whole(art)
        paths: list[str] = []

        def walk(v, p, d):
            if isinstance(v, dict) and d < 3:
                for k, x in v.items():
                    paths.append(f"{p}/{k}")
                    walk(x, f"{p}/{k}", d + 1)
        walk(data, "", 0)
        add("json_shape", ValueType.json, {"type": type(data).__name__,
                                           "size": len(data) if isinstance(data, (dict, list)) else None,
                                           "key_paths": paths[:200]}, whole)
        if isinstance(data, dict) and isinstance(data.get("title"), str):
            add("download_title", ValueType.json, {"title": data["title"], "source": _base(art)},
                self._key_ref(art, raw, "title"))
        return feats, [self._st(obs.observation_id, art, ExtractionState.ok, "json")]

    # ------------------------------------------------------------ czas i tytuł pobrania
    def _download_info(self, obs, art, raw, reader):
        d = self._load_json(raw)
        if not isinstance(d, dict):
            raise ValueError("archive_info.json nie jest obiektem")
        feats, add = self._mk(obs, art, "download")
        ts = d.get("timestamp_utc")
        if isinstance(ts, str):
            iso = _iso_utc(ts)
            add("download_time", ValueType.json, {"time": iso, "raw": ts, "source": "archive_info", "tz": "UTC",
                                                  "valid": iso is not None}, self._key_ref(art, raw, "timestamp_utc"))
        if isinstance(d.get("title"), str):
            add("download_title", ValueType.json, {"title": d["title"], "source": "archive_info"},
                self._key_ref(art, raw, "title"))
        if isinstance(d.get("url"), str):
            u = d["url"]
            add("download_url", ValueType.json, {"url_masked": mask_url(u), "url_sha256": ids.sha256_bytes(u.encode("utf-8"))},
                self._key_ref(art, raw, "url"))
        if isinstance(d.get("user_agent"), str):
            add("user_agent", ValueType.string, d["user_agent"], self._key_ref(art, raw, "user_agent"))
        return feats, [self._st(obs.observation_id, art, ExtractionState.ok, "archive_info")]

    # ------------------------------------------------------------ cookies / Web Storage
    def _storage(self, obs, art, raw, reader):
        area = STORAGE_FILES[_base(art).lower()]
        d = self._load_json(raw)
        feats, add = self._mk(obs, art, "browser_state")
        whole = self._whole(art)
        if area == "cookies":
            if isinstance(d, dict):  # {nazwa: wartość} -> tylko nazwy
                d = [{"name": k} for k in d]
            if not isinstance(d, list):
                raise ValueError("cookies: oczekiwano listy")
            names = []
            for c in d:
                if not isinstance(c, dict) or not isinstance(c.get("name"), str):
                    continue
                names.append(c["name"])
                exp = c.get("expires", c.get("expirationDate"))
                add("cookie", ValueType.json, {
                    "name": c["name"], "domain": c.get("domain"), "path": c.get("path"),
                    "secure": c.get("secure"), "http_only": c.get("httpOnly", c.get("http_only")),
                    "same_site": c.get("sameSite", c.get("same_site")),
                    "session": exp in (None, -1, 0) if "expires" in c or "expirationDate" in c else None,
                    "expires": exp if isinstance(exp, (int, float)) and exp > 0 else None,
                    "has_value": bool(c.get("value"))}, self._key_ref(art, raw, c["name"]))
            add("cookie_names", ValueType.string_list, sorted(set(names)), whole)
            add("cookie_count", ValueType.number, len(names), whole)
            n = len(names)
        else:
            if isinstance(d, list):  # [{"name":..,"value":..}] -> nazwy
                d = {x["name"]: None for x in d if isinstance(x, dict) and isinstance(x.get("name"), str)}
            if not isinstance(d, dict):
                raise ValueError(f"{area}: oczekiwano obiektu")
            keys = sorted(d)
            add("storage_keys", ValueType.json, {"area": area, "keys": keys}, whole)
            add(f"{area}_count", ValueType.number, len(keys), whole)
            n = len(keys)
        return feats, [self._st(obs.observation_id, art, ExtractionState.ok, f"{area}; pozycji={n}")]

    # ------------------------------------------------------------ manifesty sum
    def _actual(self, obs, a: Artifact, reader) -> str:
        try:
            return ids.sha256_bytes(reader.open_bytes(ArtifactRef(artifact_id=a.artifact_id)))
        except Exception:
            return a.sha256

    def _manifest(self, obs, art, raw, reader):
        n = _base(art)
        text = self._decode(raw)
        expected: dict[str, str] = {}
        if n.lower() == "sha256.json":
            d = json.loads(text)
            if not isinstance(d, dict):
                raise ValueError("sha256.json nie jest obiektem")
            expected = {str(k): str(v).lower() for k, v in d.items() if isinstance(v, str)}
        else:  # "<hash>  <nazwa>" lub sam hash (nazwa = plik bez .sha256)
            for line in text.splitlines():
                m = _HEX64.search(line)
                if not m:
                    continue
                rest = line[m.end():].strip().lstrip("*").strip()
                expected[rest or n[:-len(".sha256")]] = m.group(0).lower()
        if not expected:
            return [], [self._st(obs.observation_id, art, ExtractionState.failed, "manifest bez żadnych sum")]
        others = [a for a in obs.artifacts if a.artifact_id != art.artifact_id]
        matched, mismatched, absent = [], [], []
        for name, exp in sorted(expected.items()):
            nm = name.replace("\\", "/")
            tgt = next((a for a in others if _base(a) == nm.rsplit("/", 1)[-1]
                        or (a.origin or "").replace("\\", "/").endswith(nm)), None)
            if tgt is None:
                absent.append(name)
                continue
            act = self._actual(obs, tgt, reader)
            if act == exp:
                matched.append(name)
            else:
                mismatched.append({"name": name, "expected": exp, "actual": act, "artifact_id": tgt.artifact_id})
        feats, add = self._mk(obs, art, "manifest")
        whole = self._whole(art)
        add("manifest_entries", ValueType.json, {"count": len(expected), "names": sorted(expected)}, whole)
        verdict = "mismatch" if mismatched else ("ok" if matched and not absent else ("partial" if matched else "unverifiable"))
        add("manifest_check", ValueType.json, {"verdict": verdict, "matched": matched, "mismatched": mismatched,
                                                "not_in_observation": absent},
            whole + [ArtifactRef(artifact_id=m["artifact_id"]) for m in mismatched])
        add("manifest_consistent", ValueType.boolean, not mismatched, whole)
        if mismatched:
            return feats, [self._st(obs.observation_id, art, ExtractionState.failed,
                                    f"rozbieżność sum: {len(mismatched)} z {len(expected)} (np. {mismatched[0]['name']})")]
        return feats, [self._st(obs.observation_id, art, ExtractionState.ok,
                                f"manifest; zgodnych={len(matched)}; poza obserwacją={len(absent)}")]
