"""S06: docelowa aplikacja mobilna (sklep + ID) i publiczne powiązanie domeny z pakietem Androida (assetlinks.json).

Trzy rodzaje dowodu (evidence_type):
- store_url   — adres docelowy reklamy jest linkiem do sklepu (wejście input_url),
- page_link   — odsyłacz do sklepu znaleziony w HTML strony (słabszy: link nie dowodzi powiązania technicznego),
- assetlinks  — publiczna deklaracja domeny (/.well-known/assetlinks.json) wskazująca pakiet i odcisk certyfikatu.
"""
from __future__ import annotations

import json
import re
from typing import Any, Mapping, Optional, Sequence
from urllib.parse import parse_qs, unquote, urlsplit

from methadata.contracts import ids
from methadata.contracts.interfaces import AdapterKind, AdapterSpec
from methadata.contracts.models import (ArtifactRef, ArtifactRole, ExtractionState, ExtractionStatus, Feature,
                                        Observation, PairEvidence, Relation, TextSpan, ValueType)

ADAPTERS_VERSION = "1.0.0"
EXTRACTOR_VERSION = f"s06-mobile-app/{ADAPTERS_VERSION}"
FAMILY = "mobile_app"
_PKG = r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+"
_PKG_RE = re.compile(rf"^{_PKG}$")
_HREF = re.compile(r"""(?:href|src|content)\s*=\s*["']([^"']+)["']|(market://[^\s"'<>]+)""", re.I)
_HANDLE_ALL = "delegate_permission/common.handle_all_urls"
TECHNICAL = {"assetlinks", "store_url"}


def parse_store_url(url: str) -> Optional[dict]:
    """-> {store, app_id, url} albo None. Obsługa: Google Play, market://, App Store, AppGallery, Galaxy Store."""
    try:
        u = urlsplit(url.strip())
    except ValueError:
        return None
    host, path, q = (u.hostname or "").lower(), unquote(u.path), parse_qs(u.query)
    out = None
    if (u.scheme == "market" and host == "details") or (host == "play.google.com" and path.startswith("/store/apps/details")):
        pid = (q.get("id") or [""])[0]
        if _PKG_RE.match(pid):
            out = ("google_play", pid)
    elif host in ("apps.apple.com", "itunes.apple.com"):
        m = re.search(r"/id(\d{5,})", path) or re.search(r"[?&]id=(\d{5,})", "?" + u.query)
        if m:
            out = ("apple_app_store", m.group(1))
    elif host == "appgallery.huawei.com":
        m = re.search(r"/(?:app|#/app)/(C\d+)", path + ("#" + u.fragment if u.fragment else ""))
        if m:
            out = ("huawei_appgallery", m.group(1))
    elif host == "galaxystore.samsung.com":
        m = re.search(r"/detail/(" + _PKG + ")", path)
        if m:
            out = ("samsung_galaxy_store", m.group(1))
    return {"store": out[0], "app_id": out[1], "url": url.strip()} if out else None


def _norm_fp(fp: str) -> str:
    return re.sub(r"[^0-9A-F]", "", fp.upper())


def parse_assetlinks(doc: Any) -> list[dict]:
    """Deklaracje android_app z assetlinks.json (tylko relacja handle_all_urls) -> [{package, fingerprints}]."""
    out = []
    for st in doc if isinstance(doc, list) else []:
        if not isinstance(st, dict) or _HANDLE_ALL not in (st.get("relation") or []):
            continue
        t = st.get("target")
        if not isinstance(t, dict) or t.get("namespace") != "android_app" or not _PKG_RE.match(str(t.get("package_name", ""))):
            continue
        fps = sorted({_norm_fp(f) for f in t.get("sha256_cert_fingerprints") or [] if isinstance(f, str) and _norm_fp(f)})
        out.append({"package": t["package_name"], "fingerprints": fps})
    return out


def _host(url: Optional[str]) -> Optional[str]:
    try:
        return (urlsplit(url).hostname or None) if url else None
    except ValueError:
        return None


def _is_assetlinks(a) -> bool:
    return a.role is ArtifactRole.document and ((a.origin or "").endswith("assetlinks.json")
                                                or (a.url or "").endswith("assetlinks.json"))


class MobileAppExtractor:
    spec = AdapterSpec(id="source-mobile-app-extractor", kind=AdapterKind.extractor, version=ADAPTERS_VERSION,
                       description="Sklep i ID aplikacji z linku sklepu/HTML oraz deklaracja assetlinks.json domeny")

    def extract(self, observation: Observation, *, reader, optional: Mapping[str, Any] | None = None):
        oid = observation.observation_id
        feats: list[Feature] = []
        sts: list[ExtractionStatus] = []
        seen: set = set()

        def st(state, detail, art):
            sts.append(ExtractionStatus(observation_id=oid, artifact_id=art, extractor_version=EXTRACTOR_VERSION,
                                        state=state, detail=detail))

        def add(kind, value, vt, refs):
            key = (kind, json.dumps(value, sort_keys=True))
            if key in seen:
                return
            seen.add(key)
            feats.append(Feature(feature_id=ids.feature_id(oid, FAMILY, kind, EXTRACTOR_VERSION, value), observation_id=oid,
                                 family=FAMILY, kind=kind, value_type=vt, value=value,
                                 extractor_version=EXTRACTOR_VERSION, evidence_refs=refs))

        def emit(app, etype, art, source, locator=None, domain=None, fps=None):
            refs = [ArtifactRef(artifact_id=art.artifact_id, locator=locator)]
            add("store_name", app["store"], ValueType.string, refs)
            add("app_id", app["app_id"], ValueType.string, refs)
            ev = {"evidence_type": etype, "store": app["store"], "app_id": app["app_id"], "source": source,
                  "retrieved_at": observation.capture_time}
            if domain:
                ev["domain"] = domain
            if fps is not None:
                ev["cert_sha256"] = fps
            add("link_evidence", ev, ValueType.json, refs)

        relevant = False
        for art in observation.artifacts:
            if art.role is ArtifactRole.input_url:
                relevant = True
                app = parse_store_url(art.url or "")
                if app:
                    emit(app, "store_url", art, art.url)
                    st(ExtractionState.ok, None, art.artifact_id)
                else:
                    st(ExtractionState.ok, "adres nie jest rozpoznanym linkiem do sklepu", art.artifact_id)
            elif _is_assetlinks(art) or art.role is ArtifactRole.landing_html:
                relevant = True
                try:
                    raw = reader.open_bytes(ArtifactRef(artifact_id=art.artifact_id)).decode("utf-8-sig", "replace")
                except Exception as e:  # noqa: BLE001
                    st(ExtractionState.missing_input, f"nie można odczytać bajtów: {e}", art.artifact_id)
                    continue
                if art.role is ArtifactRole.landing_html:
                    n = 0
                    for m in _HREF.finditer(raw):
                        link = m.group(1) or m.group(2)
                        app = parse_store_url(link.replace("&amp;", "&"))
                        if app:
                            n += 1
                            emit(app, "page_link", art, art.url or observation.source_ref,
                                 TextSpan(start=m.start(), end=m.end()))
                    st(ExtractionState.ok, None if n else "brak linków do sklepów w HTML", art.artifact_id)
                    continue
                domain = _host(art.url) or _host(observation.source_ref)
                try:
                    decl = parse_assetlinks(json.loads(raw))
                except ValueError as e:
                    st(ExtractionState.failed, f"assetlinks: niepoprawny JSON ({e}); brak deklaracji", art.artifact_id)
                    continue
                if not decl:
                    st(ExtractionState.ok, "brak deklaracji android_app/handle_all_urls w assetlinks.json", art.artifact_id)
                    continue
                for d in decl:
                    emit({"store": "google_play", "app_id": d["package"]}, "assetlinks", art,
                         art.url or art.origin or observation.source_ref, domain=domain, fps=d["fingerprints"])
                    if domain:
                        add("domain_package", {"domain": domain, "package": d["package"], "cert_sha256": d["fingerprints"]},
                            ValueType.json, [ArtifactRef(artifact_id=art.artifact_id)])
                st(ExtractionState.ok, None, art.artifact_id)
        if not relevant:
            st(ExtractionState.missing_input, "brak input_url, landing_html ani assetlinks.json", None)
        return feats, sts


# ---------------------------------------------------------------------------- porównanie

def _evidence(feats: Sequence[Feature]) -> dict[tuple, list[tuple[dict, Feature]]]:
    out: dict[tuple, list] = {}
    for f in feats:
        if f.family == FAMILY and f.kind == "link_evidence" and isinstance(f.value, dict):
            out.setdefault((f.value["store"], f.value["app_id"]), []).append((f.value, f))
    return out


def _refs(items) -> list[ArtifactRef]:
    return [r for _, f in items for r in f.evidence_refs]


class MobileAppComparator:
    method_id = "source-mobile-app-compare"
    spec = AdapterSpec(id=method_id, kind=AdapterKind.comparator, version=ADAPTERS_VERSION,
                       description="Ta sama aplikacja docelowa (sklep+ID); powiązanie techniczne vs odsyłacz ze strony")

    def compare(self, a: Observation, b: Observation, features: Mapping[str, Sequence[Feature]], *, run_id: str,
                optional: Mapping[str, Any] | None = None) -> Sequence[PairEvidence]:
        (ca, oa), (cb, ob) = sorted([(a.canonical_id, a), (b.canonical_id, b)], key=lambda t: t[0])
        ea, eb = _evidence(features.get(oa.observation_id, [])), _evidence(features.get(ob.observation_id, []))

        def make(score, why, refs=None):
            return [PairEvidence(run_id=run_id, pair=(ca, cb), method_id=self.method_id,
                                 relation=Relation.campaign_candidate, score=score, evidence_refs=refs or {},
                                 explanation=why)]

        if not ea or not eb:
            return make(None, "brak podstaw: co najmniej jedna strona nie ma rozpoznanej aplikacji docelowej "
                              "(brak deklaracji to nie dowód różnych aplikacji)")
        shared = sorted(set(ea) & set(eb))
        if not shared:
            return make(0.0, f"różne aplikacje docelowe ({sorted(ea)[0][1]} vs {sorted(eb)[0][1]})",
                        {ca: _refs(x for v in ea.values() for x in v), cb: _refs(x for v in eb.values() for x in v)})
        key = shared[0]
        ia, ib = ea[key], eb[key]
        refs = {ca: _refs(ia), cb: _refs(ib)}
        name = f"{key[0]}:{key[1]}"
        ta, tb = {e["evidence_type"] for e, _ in ia}, {e["evidence_type"] for e, _ in ib}
        doms = sorted({e["domain"] for e, _ in ia + ib if e.get("domain")})
        fa = {fp for e, _ in ia for fp in e.get("cert_sha256") or []}
        fb = {fp for e, _ in ib for fp in e.get("cert_sha256") or []}
        if fa and fb and not (fa & fb):
            return make(0.4, f"ten sam pakiet {name}, ale różne odciski certyfikatu w deklaracjach "
                             "(możliwa podróbka/repack): mylące dopasowanie", refs)
        if ta & TECHNICAL and tb & TECHNICAL:
            how = "powiązanie techniczne" + (f" (assetlinks domen: {', '.join(doms)})" if doms else "")
            fp = " zgodny odcisk certyfikatu" if fa & fb else ""
            return make(1.0, f"ta sama aplikacja {name}: {how};{fp}".rstrip(";") + " [dowód: "
                        f"{'+'.join(sorted(ta))} / {'+'.join(sorted(tb))}]", refs)
        return make(0.6, f"ta sama aplikacja {name}, ale co najmniej jedna strona wskazuje ją tylko odsyłaczem "
                         f"ze strony (page_link), bez powiązania technicznego [{'+'.join(sorted(ta))} / "
                         f"{'+'.join(sorted(tb))}]", refs)
