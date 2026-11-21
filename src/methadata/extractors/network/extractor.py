"""Ekstraktor sieciowy: HAR (żądania, odpowiedzi, przekierowania, inicjatory, czasy) oraz dns_ssl.json.

Zasady:
- każdy wpis HAR to jedno żądanie i jedna krawędź inicjatora (równoległe żądania nie są scalane);
- łańcuch 3xx powstaje wyłącznie z jawnego 3xx + Location/redirectURL i obserwowanego żądania celu;
- wartości sesyjne (identyfikatory kliknięcia, utm_visit, ciasteczka, nagłówki autoryzacji) nie trafiają do cech;
- treści odpowiedzi są utrwalane jako artefakty pochodne (har_response) przez DerivedStore.
"""
from __future__ import annotations

import base64
import binascii
import json
from typing import Any, Mapping, Optional
from urllib.parse import parse_qsl, urljoin, urlsplit

from methadata.contracts import (AdapterKind, AdapterSpec, ArtifactRef, ExtractionState, ExtractionStatus, Feature,
                                 ids)
from methadata.contracts.models import ArtifactRole, HarEntry, ParentRelation, ValueType
from methadata.contracts.urls import MASKED, SENSITIVE_PARAMS, mask_url

EXTRACTOR_VERSION = "network-1"
FAMILY = "network"
SESSION_PARAMS = SENSITIVE_PARAMS | {"utm_visit", "visit", "visitor", "uuid", "uid", "cid", "cookie", "csrf", "nonce",
                                     "_ga", "_gl", "ts", "timestamp", "t", "cb", "rnd", "random"}
TECH_HEADERS = ("server", "content-type", "content-encoding", "x-powered-by", "via", "cache-control")


def _san(url: Optional[str]) -> Optional[str]:
    """URL bez wartości sesyjnych: maska kliknięć + parametry sesyjne; fragment pominięty."""
    if not url:
        return url
    p = urlsplit(mask_url(url))
    if not p.query:
        return p._replace(fragment="").geturl()
    q = "&".join(f"{k}={MASKED if k.lower() in SESSION_PARAMS else v}"
                 for k, v in parse_qsl(p.query, keep_blank_values=True))
    return p._replace(query=q, fragment="").geturl()


def _host(url: str) -> Optional[str]:
    try:
        return urlsplit(url).hostname
    except ValueError:
        return None


def _hdr(headers: list, name: str) -> Optional[str]:
    for h in headers or []:
        if isinstance(h, dict) and str(h.get("name", "")).lower() == name:
            return str(h.get("value", ""))
    return None


def _num(v: Any) -> Optional[float]:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0 else None


def _initiator(e: dict) -> dict:
    ini = e.get("_initiator") or {}
    url = ini.get("url")
    if not url:
        frames = ((ini.get("stack") or {}).get("callFrames")) or []
        for fr in frames:
            if fr.get("url"):
                url = fr["url"]
                break
    return {"type": ini.get("type"), "url": url}


def _content(e: dict) -> tuple[str, Optional[bytes]]:
    """(stan, bajty): none | empty | text | base64 | invalid."""
    c = (e.get("response") or {}).get("content") or {}
    text = c.get("text")
    if text is None:
        return "none", None
    if not isinstance(text, str):
        return "invalid", None
    if text == "":
        return "empty", b""
    if c.get("encoding") == "base64":
        try:
            return "base64", base64.b64decode(text, validate=False)
        except (binascii.Error, ValueError):
            return "invalid", None
    return "text", text.encode("utf-8")


def parse_redirect_chains(entries: list[dict]) -> list[list[dict]]:
    """Łańcuchy z jawnych 3xx. Każde ogniwo: {index, status, url, location, observed}. Ogniwo-cel to następne
    nieprzypisane żądanie o dokładnie tym adresie, rozpoczęte nie wcześniej niż źródło. Brak celu w HAR =
    ogniwo końcowe z observed=False (dowód tylko Location)."""
    reqs = []
    for i, e in enumerate(entries):
        r, s = e.get("request") or {}, e.get("response") or {}
        reqs.append({"i": i, "url": r.get("url") or "", "status": s.get("status") or 0,
                     "loc": s.get("redirectURL") or _hdr(s.get("headers"), "location") or "",
                     "t": e.get("startedDateTime") or ""})
    claimed: set[int] = set()
    nxt: dict[int, Optional[int]] = {}
    for r in sorted((r for r in reqs if 300 <= r["status"] < 400 and r["loc"]), key=lambda r: (r["t"], r["i"])):
        target = urljoin(r["url"], r["loc"])
        r["target"] = target
        cand = [c for c in reqs if c["i"] not in claimed and c["i"] != r["i"] and c["url"] == target
                and c["t"] >= r["t"]]
        cand.sort(key=lambda c: (c["t"], c["i"]))
        if cand:
            claimed.add(cand[0]["i"])
            nxt[r["i"]] = cand[0]["i"]
        else:
            nxt[r["i"]] = None
    chains = []
    for r in reqs:
        if "target" not in r or r["i"] in claimed:
            continue
        chain, cur, seen = [], r, set()
        while cur is not None and cur["i"] not in seen:
            seen.add(cur["i"])
            is_redir = "target" in cur
            chain.append({"index": cur["i"], "status": cur["status"], "url": cur["url"],
                          "location": cur.get("target") if is_redir else None, "observed": True})
            if not is_redir:
                break
            n = nxt.get(cur["i"])
            if n is None:
                chain.append({"index": None, "status": None, "url": cur["target"], "location": None,
                              "observed": False})
                break
            cur = reqs[n]
        chains.append(chain)
    return chains


class NetworkExtractor:
    spec = AdapterSpec(id="network-extractor", kind=AdapterKind.extractor, version=EXTRACTOR_VERSION,
                       description="HAR: żądania, odpowiedzi, przekierowania 3xx, inicjatory, czasy; dns_ssl.json")

    def __init__(self, store: Any = None) -> None:
        self.store = store

    # ------------------------------------------------------------------ pomocnicze
    def _feat(self, obs_id: str, kind: str, vt: ValueType, value: Any, refs: list[ArtifactRef]) -> Feature:
        return Feature(feature_id=ids.feature_id(obs_id, FAMILY, kind, EXTRACTOR_VERSION, value),
                       observation_id=obs_id, family=FAMILY, kind=kind, value_type=vt, value=value,
                       extractor_version=EXTRACTOR_VERSION, evidence_refs=refs)

    def _status(self, obs_id, state, detail=None, art=None) -> ExtractionStatus:
        return ExtractionStatus(observation_id=obs_id, artifact_id=art, extractor_version=EXTRACTOR_VERSION,
                                state=state, detail=detail)

    # ------------------------------------------------------------------ główne wejście
    def extract(self, observation, *, reader, optional: Mapping[str, Any] | None = None):
        oid = observation.observation_id
        feats: list[Feature] = []
        stats: list[ExtractionStatus] = []
        hars = [a for a in observation.artifacts if a.role is ArtifactRole.har]
        dns = [a for a in observation.artifacts if a.role is not ArtifactRole.har and (a.origin or a.local_ref or "")
               .replace("\\", "/").rsplit("/", 1)[-1] == "dns_ssl.json"]
        if not hars:
            stats.append(self._status(oid, ExtractionState.missing_input, "brak artefaktu HAR"))
        store = self.store or (reader if hasattr(reader, "put_derived") else None)
        for a in hars:
            self._har(observation, a, reader, feats, stats, store)
        if not dns:
            stats.append(self._status(oid, ExtractionState.missing_input, "brak dns_ssl.json"))
        for a in dns:
            self._dns(observation, a, reader, feats, stats)
        return feats, stats

    # ------------------------------------------------------------------ HAR
    def _har(self, obs, art, reader, feats, stats, store):
        oid = obs.observation_id
        try:
            doc = json.loads(reader.open_bytes(ArtifactRef(artifact_id=art.artifact_id)).decode("utf-8-sig"))
            entries = doc["log"]["entries"]
            if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
                raise ValueError("log.entries nie jest listą obiektów")
        except Exception as ex:  # niezaufany plik: każdy błąd to status, nie wyjątek
            stats.append(self._status(oid, ExtractionState.failed, f"HAR nieczytelny: {type(ex).__name__}: {ex}"[:300],
                                      art.artifact_id))
            return
        ref = lambda i: ArtifactRef(artifact_id=art.artifact_id, locator=HarEntry(index=i))  # noqa: E731
        hosts: dict[str, None] = {}
        ips: dict[str, None] = {}
        ctypes: dict[str, None] = {}
        no_body = unsaved = bad = 0
        derived: dict[int, str] = {}
        for i, e in enumerate(entries):
            req, resp = e.get("request") or {}, e.get("response") or {}
            url = req.get("url") or ""
            state, data = _content(e)
            if state in ("none", "invalid"):
                no_body += 1
                bad += state == "invalid"
            mime = (resp.get("content") or {}).get("mimeType") or _hdr(resp.get("headers"), "content-type")
            if data and store is not None:
                d = store.put_derived(observation_id=oid, role=ArtifactRole.resource, data=data,
                                           media_type=(mime or None), url=url or None, parent=ref(i),
                                           parent_relation=ParentRelation.har_response, origin=f"har:{i}")
                derived[i] = d.artifact_id
            elif data:
                unsaved += 1
            t = e.get("timings") or {}
            tech = {h: _hdr(resp.get("headers"), h) for h in TECH_HEADERS if _hdr(resp.get("headers"), h) is not None}
            ini = _initiator(e)
            value = {
                "index": i, "method": req.get("method"), "url": _san(url), "host": _host(url),
                "path": urlsplit(url).path if url else None,
                "query_keys": sorted({k for k, _ in parse_qsl(urlsplit(url).query, keep_blank_values=True)}),
                "status": resp.get("status"), "mime": (mime or "").split(";")[0].strip() or None,
                "resource_type": e.get("_resourceType"), "ip": e.get("serverIPAddress") or None,
                "started": e.get("startedDateTime"), "time_ms": _num(e.get("time")),
                "timings_ms": {k: _num(t.get(k)) for k in ("blocked", "dns", "connect", "ssl", "send", "wait", "receive")
                               if _num(t.get(k)) is not None},
                "body_size": (resp.get("content") or {}).get("size"), "body_state": state,
                "body_sha256": ids.sha256_bytes(data) if data else None,
                "body_artifact_id": derived.get(i), "headers": tech,
                "initiator_type": ini["type"], "initiator_url": _san(ini["url"]),
            }
            feats.append(self._feat(oid, "har_request", ValueType.json, value, [ref(i)]))
            if ini["url"]:
                edge = {"index": i, "from": _san(ini["url"]), "to": _san(url), "type": ini["type"]}
                feats.append(self._feat(oid, "har_initiator_edge", ValueType.json, edge, [ref(i)]))
            h = _host(url)
            if h:
                hosts[h] = None
            if e.get("serverIPAddress"):
                ips[e["serverIPAddress"]] = None
            if value["mime"]:
                ctypes[value["mime"]] = None
        for chain in parse_redirect_chains(entries):
            hops = [{**h, "url": _san(h["url"]), "location": _san(h["location"])} for h in chain]
            refs = [ref(h["index"]) for h in chain if h["index"] is not None]
            feats.append(self._feat(oid, "redirect_chain", ValueType.json,
                                    {"hops": hops, "complete": all(h["observed"] for h in chain)}, refs))
        for kind, vals in (("har_hosts", hosts), ("har_ips", ips), ("har_content_types", ctypes)):
            if vals:
                feats.append(self._feat(oid, kind, ValueType.string_list, sorted(vals),
                                        [ArtifactRef(artifact_id=art.artifact_id)]))
        notes = []
        if no_body:
            notes.append(f"{no_body} wpisów bez treści odpowiedzi" + (f" ({bad} niepoprawnych)" if bad else ""))
        if unsaved:
            notes.append(f"{unsaved} treści nieutrwalonych (brak magazynu pochodnych)")
        if not entries:
            notes.append("HAR bez wpisów")
        stats.append(self._status(oid, ExtractionState.ok, "; ".join(notes) or None, art.artifact_id))

    # ------------------------------------------------------------------ DNS/TLS
    def _dns(self, obs, art, reader, feats, stats):
        oid = obs.observation_id
        aref = [ArtifactRef(artifact_id=art.artifact_id)]
        try:
            d = json.loads(reader.open_bytes(aref[0]).decode("utf-8-sig"))
            if not isinstance(d, dict):
                raise ValueError("oczekiwano obiektu")
        except Exception as ex:
            stats.append(self._status(oid, ExtractionState.failed, f"dns_ssl.json nieczytelny: {type(ex).__name__}"[:300],
                                      art.artifact_id))
            return
        if d.get("hostname"):
            feats.append(self._feat(oid, "dns_host", ValueType.string, str(d["hostname"]).lower(), aref))
        addrs = [a for a in ([d.get("ip")] + list(d.get("all_addresses") or [])) if isinstance(a, str)]
        if addrs:
            feats.append(self._feat(oid, "dns_ips", ValueType.string_list, sorted(set(addrs)), aref))

        def name(rdns, key):
            for rdn in rdns or []:
                for k, v in rdn:
                    if k == key:
                        return v
            return None
        ssl = d.get("ssl")
        if isinstance(ssl, dict):
            try:
                cert = {"subject_cn": name(ssl.get("subject"), "commonName"),
                        "issuer_cn": name(ssl.get("issuer"), "commonName"),
                        "issuer_org": name(ssl.get("issuer"), "organizationName"),
                        "serial": ssl.get("serialNumber"), "not_before": ssl.get("notBefore"),
                        "not_after": ssl.get("notAfter"), "version": ssl.get("version"),
                        "san": sorted(v.lower() for k, v in (ssl.get("subjectAltName") or []) if k == "DNS")}
            except (TypeError, ValueError) as ex:
                stats.append(self._status(oid, ExtractionState.failed, f"pole TLS: {type(ex).__name__}", art.artifact_id))
                return
            feats.append(self._feat(oid, "tls_cert", ValueType.json, cert, aref))
            stats.append(self._status(oid, ExtractionState.ok, None, art.artifact_id))
        else:
            stats.append(self._status(oid, ExtractionState.ok, "brak pola ssl (brak danych TLS)", art.artifact_id))
