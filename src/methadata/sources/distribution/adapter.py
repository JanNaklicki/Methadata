"""S09: sprzedawcy i dystrybucja reklam (ads.txt / app-ads.txt, sellers.json, zapisany łańcuch sprzedaży schain).

Dwa rozłączne typy relacji (pole `relation_type` cechy i `edge_type` krawędzi grafu):
- authorized_for_publisher — wpis ads.txt: wydawca UPOWAŻNIA konto sprzedawcy w systemie reklamowym (zdolność, nie emisja),
- observed_in_delivery     — węzeł łańcucha sprzedaży (schain) zapisanego przy KONKRETNEJ emisji (zaobserwowany).
sellers.json to katalog systemu reklamowego: tylko uzupełnia wpisy (nazwa, typ, poufność), nie jest dowodem emisji.
Konto bez nazwy systemu reklamowego jest niejednoznaczne (to samo ID bywa w wielu systemach) — flaga `ambiguous`.
Wejścia: artefakty role=document rozpoznawane po nazwie (ads.txt, app-ads.txt, sellers.json, schain*.json) lub po treści JSON.
Bez dostępu do sieci: moduł nic nie pobiera, czyta wyłącznie zapisane artefakty.
"""
from __future__ import annotations

import json
import posixpath
import re
from typing import Any, Iterable, Mapping, Optional, Sequence
from urllib.parse import urlsplit

from methadata.contracts import ids
from methadata.contracts.interfaces import AdapterKind, AdapterSpec
from methadata.contracts.models import (ArtifactRef, ArtifactRole, ExtractionState, ExtractionStatus, Feature,
                                        Observation, PairEvidence, Relation, ValueType)

ADAPTERS_VERSION = "1.0.0"
EXTRACTOR_VERSION = f"s09-distribution/{ADAPTERS_VERSION}"
FAMILY = "distribution"
AUTHORIZED, OBSERVED = "authorized_for_publisher", "observed_in_delivery"
_VARS = {"CONTACT", "SUBDOMAIN", "OWNERDOMAIN", "MANAGERDOMAIN", "INVENTORYPARTNERDOMAIN"}
_DOMAIN = re.compile(r"^[a-z0-9]([a-z0-9\-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9\-]*[a-z0-9])?)+$")
MAX_RECORDS = 5000


def _dom(s: Any) -> Optional[str]:
    s = str(s or "").strip().lower().rstrip(".")
    return s if _DOMAIN.match(s) else None


# ---------------------------------------------------------------------------- parsery

def parse_ads_txt(text: str) -> dict:
    """-> {records:[{ad_system, account_id, relationship, cert_authority_id, ambiguous}], variables:{}, issues:[..]}.
    Wiersze bez nazwy systemu (`,123,DIRECT` lub `123,DIRECT`) dają ad_system=None i ambiguous=True."""
    records, variables, issues = [], {}, []
    for n, raw in enumerate(text.lstrip("﻿").splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if "=" in line and "," not in line.split("=", 1)[0]:
            k, v = line.split("=", 1)
            if k.strip().upper() in _VARS:
                variables.setdefault(k.strip().upper(), []).append(v.strip())
                continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 2 and parts[1].upper() in ("DIRECT", "RESELLER"):
            parts = ["", parts[0], parts[1]]
        if len(parts) < 3 or parts[2].upper() not in ("DIRECT", "RESELLER") or not parts[1]:
            issues.append(f"wiersz {n}: nierozpoznany wpis")
            continue
        system = _dom(parts[0])
        if parts[0] and system is None:
            issues.append(f"wiersz {n}: niepoprawna nazwa systemu {parts[0]!r}")
            continue
        records.append({"ad_system": system, "account_id": parts[1], "relationship": parts[2].upper(),
                        "cert_authority_id": (parts[3] or None) if len(parts) > 3 else None, "ambiguous": system is None})
        if len(records) >= MAX_RECORDS:
            issues.append(f"obcięto po {MAX_RECORDS} wpisach")
            break
    return {"records": records, "variables": variables, "issues": issues}


def parse_sellers_json(doc: Any) -> dict:
    """-> {sellers: {seller_id: {...}}, contact_email, identifiers}. Niepoprawna struktura -> ValueError."""
    if not isinstance(doc, dict) or not isinstance(doc.get("sellers"), list):
        raise ValueError("brak listy sellers")
    out = {}
    for s in doc["sellers"][:200000]:
        if not isinstance(s, dict) or s.get("seller_id") in (None, ""):
            continue
        conf = s.get("is_confidential")
        out[str(s["seller_id"]).strip()] = {
            "seller_type": str(s.get("seller_type") or "").upper() or None,
            "name": None if conf in (1, "1", True) else s.get("name"),
            "domain": None if conf in (1, "1", True) else _dom(s.get("domain")),
            "is_confidential": conf in (1, "1", True), "is_passthrough": s.get("is_passthrough") in (1, "1", True)}
    return {"sellers": out, "contact_email": doc.get("contact_email"), "version": doc.get("version")}


def parse_schain(doc: Any) -> dict:
    """Łańcuch sprzedaży: obiekt OpenRTB {ver, complete, nodes:[{asi,sid,hp,rid,name,domain}]} (także owinięty w
    {schain:...} / ext.schain) albo zapis tekstowy `1.0,1!asi,sid,hp,rid,name,domain!...`. -> {complete, ver, nodes}."""
    if isinstance(doc, str):
        head, *nodes = doc.strip().split("!")
        h = head.split(",")
        doc = {"ver": h[0], "complete": h[1] if len(h) > 1 else None,
               "nodes": [dict(zip(("asi", "sid", "hp", "rid", "name", "domain"), [p for p in n.split(",")])) for n in nodes]}
    for _ in range(3):
        if isinstance(doc, dict) and "nodes" not in doc:
            sub = [x.get("schain") for x in (doc, doc.get("ext"), doc.get("source")) if isinstance(x, dict)]
            doc = next((x for x in sub if x), doc)
    if not isinstance(doc, dict) or not isinstance(doc.get("nodes"), list):
        raise ValueError("brak nodes w łańcuchu sprzedaży")
    nodes = []
    for i, n in enumerate(doc["nodes"]):
        if not isinstance(n, dict):
            continue
        nodes.append({"hop": i, "ad_system": _dom(n.get("asi")), "seller_id": str(n.get("sid") or "").strip() or None,
                      "hp": n.get("hp") in (1, "1", True), "rid": n.get("rid") or None, "name": n.get("name") or None,
                      "domain": _dom(n.get("domain"))})
    complete = doc.get("complete")
    return {"complete": None if complete in (None, "") else str(complete) in ("1", "True", "true"),
            "ver": str(doc.get("ver") or "") or None, "nodes": nodes}


# ---------------------------------------------------------------------------- ekstraktor

def _base(a) -> str:
    for s in (a.origin, a.url):
        if s:
            try:
                p = urlsplit(s).path if "://" in s else s
            except ValueError:
                p = s
            return posixpath.basename(p).lower()
    return ""


def _host(url: Optional[str]) -> Optional[str]:
    try:
        return _dom(urlsplit(url).hostname) if url else None
    except ValueError:
        return None


def _kind(a, raw: str) -> Optional[str]:
    b = _base(a)
    if b in ("ads.txt", "app-ads.txt"):
        return "ads_txt"
    if b == "sellers.json":
        return "sellers"
    if "schain" in b or "sales_chain" in b or "supplychain" in b:
        return "schain"
    if raw.lstrip().startswith(("{", "[")):
        try:
            d = json.loads(raw)
        except ValueError:
            return None
        if isinstance(d, dict):
            if isinstance(d.get("sellers"), list):
                return "sellers"
            if "nodes" in d or "schain" in d or (isinstance(d.get("ext"), dict) and "schain" in d["ext"]):
                return "schain"
    return None


class DistributionExtractor:
    spec = AdapterSpec(id="source-distribution-extractor", kind=AdapterKind.extractor, version=ADAPTERS_VERSION,
                       description="Pary system reklamowy–konto z ads.txt (upoważnienie wydawcy), katalog sellers.json "
                                   "i zapisany łańcuch sprzedaży emisji (zaobserwowany)")

    def extract(self, observation: Observation, *, reader, optional: Mapping[str, Any] | None = None):
        oid = observation.observation_id
        feats: list[Feature] = []
        sts: list[ExtractionStatus] = []
        seen: set = set()

        def st(state, detail, art):
            sts.append(ExtractionStatus(observation_id=oid, artifact_id=art, extractor_version=EXTRACTOR_VERSION,
                                        state=state, detail=detail))

        def add(kind, value, art):
            key = (kind, json.dumps(value, sort_keys=True, default=str))
            if key in seen:
                return
            seen.add(key)
            feats.append(Feature(feature_id=ids.feature_id(oid, FAMILY, kind, EXTRACTOR_VERSION, value), observation_id=oid,
                                 family=FAMILY, kind=kind, value_type=ValueType.json, value=value,
                                 extractor_version=EXTRACTOR_VERSION, evidence_refs=[ArtifactRef(artifact_id=art.artifact_id)]))

        parsed: dict[str, list] = {"ads_txt": [], "sellers": [], "schain": []}
        relevant = False
        for art in observation.artifacts:
            if art.role is not ArtifactRole.document:
                continue
            try:
                raw = reader.open_bytes(ArtifactRef(artifact_id=art.artifact_id)).decode("utf-8-sig", "replace")
            except Exception as e:  # noqa: BLE001
                if _base(art) in ("ads.txt", "app-ads.txt", "sellers.json"):
                    relevant = True
                    st(ExtractionState.missing_input, f"nie można odczytać bajtów: {e}", art.artifact_id)
                continue
            kind = _kind(art, raw)
            if kind is None:
                continue
            relevant = True
            src = art.url or art.origin or observation.source_ref
            try:
                if kind == "ads_txt":
                    p = parse_ads_txt(raw)
                    parsed["ads_txt"].append((art, src, p))
                    st(ExtractionState.ok, "; ".join(p["issues"][:5]) or (None if p["records"] else "brak wpisów w ads.txt"),
                       art.artifact_id)
                elif kind == "sellers":
                    p = parse_sellers_json(json.loads(raw))
                    parsed["sellers"].append((art, src, p))
                    st(ExtractionState.ok, None, art.artifact_id)
                else:
                    p = parse_schain(raw.strip() if not raw.lstrip().startswith(("{", "[")) else json.loads(raw))
                    parsed["schain"].append((art, src, p))
                    st(ExtractionState.ok, None if p["nodes"] else "łańcuch bez węzłów", art.artifact_id)
            except ValueError as e:
                st(ExtractionState.failed, f"{kind}: niepoprawne dane ({e})", art.artifact_id)
        if not relevant:
            st(ExtractionState.missing_input, "brak ads.txt, sellers.json ani łańcucha sprzedaży", None)
            return feats, sts

        # katalog sellers.json: system = domena hosta artefaktu (np. https://google.com/sellers.json)
        directory: dict[tuple, dict] = {}
        for art, src, p in parsed["sellers"]:
            sysd = _host(art.url) or _host(src)
            for sid, rec in p["sellers"].items():
                directory[(sysd, sid)] = rec
        by_sid: dict[str, list] = {}
        for (sysd, sid), rec in directory.items():
            by_sid.setdefault(sid, []).append((sysd, rec))

        def lookup(system, sid):
            if system and (system, sid) in directory:
                return directory[(system, sid)]
            return None

        auth_pairs = set()
        for art, src, p in parsed["ads_txt"]:
            pub = _host(art.url) or _host(src) or (p["variables"].get("OWNERDOMAIN") or [None])[0]
            for r in p["records"]:
                auth_pairs.add((r["ad_system"], r["account_id"]))
                add("authorized_seller", {**r, "relation_type": AUTHORIZED, "publisher_domain": pub, "source": src,
                                          "retrieved_at": observation.capture_time,
                                          "directory": lookup(r["ad_system"], r["account_id"]),
                                          "note": "konto bez nazwy systemu: niejednoznaczne" if r["ambiguous"] else None},
                    art)
            for k, v in p["variables"].items():
                add("ads_variable", {"name": k, "values": v, "source": src}, art)
        have_ads = bool(parsed["ads_txt"])
        for art, src, p in parsed["schain"]:
            hops = []
            for n in p["nodes"]:
                authorized = None
                if have_ads and n["ad_system"] and n["seller_id"]:
                    authorized = (n["ad_system"], n["seller_id"]) in auth_pairs
                val = {**n, "relation_type": OBSERVED, "complete": p["complete"], "source": src,
                       "retrieved_at": observation.capture_time, "authorized_by_publisher": authorized,
                       "ambiguous": n["ad_system"] is None, "directory": lookup(n["ad_system"], n["seller_id"])}
                hops.append(val)
                add("observed_seller", val, art)
            add("sales_chain", {"ver": p["ver"], "complete": p["complete"], "length": len(hops), "source": src,
                                "retrieved_at": observation.capture_time, "relation_type": OBSERVED}, art)
        return feats, sts


# ---------------------------------------------------------------------------- graf

def seller_graph_edges(features: Iterable[Feature]) -> list[dict]:
    """Krawędzie obserwacja→konto sprzedawcy z jawnym `edge_type` (authorized_for_publisher | observed_in_delivery)."""
    out = []
    for f in features:
        if f.family != FAMILY or f.kind not in ("authorized_seller", "observed_seller") or not isinstance(f.value, dict):
            continue
        v = f.value
        acct = v.get("account_id") if f.kind == "authorized_seller" else v.get("seller_id")
        if not acct:
            continue
        out.append({"source": f.observation_id, "target": f"seller:{v.get('ad_system') or '?'}:{acct}",
                    "edge_type": v["relation_type"], "ambiguous": bool(v.get("ambiguous")),
                    "feature_id": f.feature_id})
    return out


# ---------------------------------------------------------------------------- porównanie

def _sets(feats: Sequence[Feature]) -> dict:
    auth, obs, acct, refs = set(), set(), set(), []
    for f in feats:
        if f.family != FAMILY or not isinstance(f.value, dict):
            continue
        v = f.value
        if f.kind == "authorized_seller":
            (acct.add(v["account_id"]) if v["ambiguous"] else auth.add((v["ad_system"], v["account_id"])))
        elif f.kind == "observed_seller" and v.get("seller_id"):
            (acct.add(v["seller_id"]) if v["ambiguous"] else obs.add((v["ad_system"], v["seller_id"])))
        else:
            continue
        refs += f.evidence_refs
    return {"auth": auth, "obs": obs, "acct": acct, "refs": refs}


def _n(pair) -> str:
    return f"{pair[0]}/{pair[1]}"


class DistributionComparator:
    method_id = "source-distribution-compare"
    spec = AdapterSpec(id=method_id, kind=AdapterKind.comparator, version=ADAPTERS_VERSION,
                       description="Wspólny sprzedawca: zaobserwowany w emisji vs tylko upoważniony przez wydawcę")

    def compare(self, a: Observation, b: Observation, features: Mapping[str, Sequence[Feature]], *, run_id: str,
                optional: Mapping[str, Any] | None = None) -> Sequence[PairEvidence]:
        (ca, oa), (cb, ob) = sorted([(a.canonical_id, a), (b.canonical_id, b)], key=lambda t: t[0])
        sa, sb = _sets(features.get(oa.observation_id, [])), _sets(features.get(ob.observation_id, []))

        def make(score, why):
            return [PairEvidence(run_id=run_id, pair=(ca, cb), method_id=self.method_id,
                                 relation=Relation.campaign_candidate, score=score, explanation=why,
                                 evidence_refs={ca: sa["refs"], cb: sb["refs"]})]

        empty = lambda s: not (s["auth"] or s["obs"] or s["acct"])  # noqa: E731
        if empty(sa) or empty(sb):
            return make(None, "brak podstaw: co najmniej jedna strona nie ma danych o sprzedawcach "
                              "(brak ads.txt/łańcucha to nie dowód różnych sprzedawców)")
        both_obs = sorted(sa["obs"] & sb["obs"])
        if both_obs:
            return make(0.85, f"ten sam sprzedawca zaobserwowany w obu emisjach [{OBSERVED}]: "
                              f"{', '.join(_n(p) for p in both_obs[:3])}")
        cross = sorted((sa["obs"] & sb["auth"]) | (sb["obs"] & sa["auth"]))
        if cross:
            return make(0.5, f"sprzedawca zaobserwowany w jednej emisji jest tylko upoważniony przez wydawcę drugiej "
                             f"[{OBSERVED} vs {AUTHORIZED}]: {', '.join(_n(p) for p in cross[:3])}")
        both_auth = sorted(sa["auth"] & sb["auth"])
        if both_auth:
            return make(0.35, f"to samo konto upoważnione przez obu wydawców [{AUTHORIZED}], bez dowodu emisji "
                              f"(zdolność, nie obserwacja; konta dużych systemów są współdzielone): "
                              f"{', '.join(_n(p) for p in both_auth[:3])}")
        shared_acct = sorted((sa["acct"] & (sb["acct"] | {p[1] for p in sb["auth"] | sb["obs"]}))
                             | (sb["acct"] & {p[1] for p in sa["auth"] | sa["obs"]}))
        if shared_acct:
            return make(0.1, f"to samo ID konta {shared_acct[0]!r}, ale co najmniej po jednej stronie bez nazwy systemu "
                             "reklamowego (ID bez systemu może należeć do dowolnego): niejednoznaczne, mylące dopasowanie")
        return make(0.0, "brak wspólnych sprzedawców w danych obu stron")
