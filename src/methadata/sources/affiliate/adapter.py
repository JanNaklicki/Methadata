"""S05: identyfikator partnera / kod polecający wraz z nazwą programu afiliacyjnego.

Klucz złożony partnera: "<program>:<partner_id>". Rodzaje kodu (role):
- partner_id — identyfikator partnera w programie (silny sygnał wspólnego operatora, o ile program ten sam),
- promo_code — kod rabatowy/promocyjny (ogólnopromocyjny: używany przez wielu, nie identyfikuje partnera),
- referral_code — kod polecający z linku/opisu bez rozpoznanego programu sieciowego (słaby, zależy od sklepu).
Pośrednik (network) łączy domeny przekierowujące tego samego operatora (np. awin1.com i zenaps.com -> awin).
Konfiguracja: stałe PROGRAMS / PROMO_PARAMS / REFERRAL_PARAMS poniżej. Treści stron są niezaufane: tylko parsowanie tekstu.
"""
from __future__ import annotations

import json
import re
from html import unescape
from typing import Any, Mapping, Optional, Sequence
from urllib.parse import parse_qs, urlsplit

from methadata.contracts import ids
from methadata.contracts.interfaces import AdapterKind, AdapterSpec
from methadata.contracts.models import (ArtifactRef, ArtifactRole, ExtractionState, ExtractionStatus, Feature,
                                        Observation, PairEvidence, Relation, TextSpan, ValueType)

ADAPTERS_VERSION = "1.0.0"
EXTRACTOR_VERSION = f"s05-affiliate/{ADAPTERS_VERSION}"
FAMILY = "affiliate"

# Programy sieciowe: domeny przekierowań (suffix) -> parametry partnera/sprzedawcy. Pośrednik = network.
PROGRAMS: dict[str, dict] = {
    "awin": {"domains": ["awin1.com", "zenaps.com"], "partner": ["awinaffid", "affid"], "merchant": ["awinmid", "mid"]},
    "cj": {"domains": ["anrdoezrs.net", "jdoqocy.com", "tkqlhce.com", "dpbolvw.net", "kqzyfj.com", "commission-junction.com"],
           "path_partner": r"/click-(\d+)-\d+", "partner": [], "merchant": []},
    "rakuten": {"domains": ["linksynergy.com"], "partner": ["id"], "merchant": ["mid"]},
    "clickbank": {"domains": ["hop.clickbank.net", "clickbank.net"], "partner": ["affiliate", "aff"], "merchant": ["vendor"],
                  "host_partner": r"^([a-z0-9]+)\.([a-z0-9]+)\.hop\.clickbank\.net$"},
    "impact": {"domains": ["sjv.io", "pxf.io", "ojrq.net", "impactradius.com", "evyy.net"],
               "path_partner": r"/c/(\d+)/\d+/\d+", "partner": [], "merchant": []},
    "partnerize": {"domains": ["prf.hn"], "path_partner": r"/click/camref:([A-Za-z0-9]+)", "partner": [], "merchant": []},
    "amazon_associates": {"domains": [], "host_re": r"(^|\.)amazon\.[a-z.]+$|(^|\.)amzn\.to$", "partner": ["tag"],
                          "merchant": []},
    "admitad": {"domains": ["ad.admitad.com", "admitad.com"], "path_partner": r"/g/([a-z0-9]+)/", "partner": ["subid"],
                "merchant": []},
    "tradedoubler": {"domains": ["tradedoubler.com", "clk.tradedoubler.com"], "partner": ["a", "p"], "merchant": []},
}
GENERIC_PARTNER_PARAMS = ["aff_id", "affid", "affiliate_id", "affiliate", "a_aid", "aff", "partner_id", "pid", "clickref", "tag"]
PROMO_PARAMS = ["promo", "promocode", "promo_code", "coupon", "coupon_code", "couponcode", "voucher", "discount",
                "discount_code", "kod", "kod_rabatowy", "code"]
REFERRAL_PARAMS = ["ref", "ref_code", "refcode", "referral", "referral_code", "referrer_id", "invite", "invite_code"]

_CODE = r"[A-Za-z0-9][A-Za-z0-9_-]{2,31}"
_CODE_OK = re.compile(rf"^{_CODE}$")
_ID_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")  # identyfikator partnera w znanej sieci może być krótki
# Zdania typu "użyj kodu ABC10", "kod rabatowy: ABC10", "promo code ABC10", "kod polecający XYZ".
_TXT = re.compile(
    r"(?P<lab>kod\s+(?:rabatowy|promocyjny|polecaj[ąa]cy|partnerski|afiliacyjny)|u[żz]yj\s+kodu|z\s+kodem|"
    r"promo(?:tion)?\s+code|discount\s+code|coupon(?:\s+code)?|referral\s+code|invite\s+code|affiliate\s+id)"
    rf"\s*[:=\-]?\s*[\"„'«]?(?P<code>{_CODE})", re.I)
_REF_LABEL = re.compile(r"polecaj|referral|invite|affiliate|partner", re.I)
_HREF = re.compile(r"""href\s*=\s*["']([^"']+)["']""", re.I)
_STOPWORDS = {"code", "kod", "kodu", "rabatowy", "promo", "https", "http", "www", "click", "here", "link"}


def _network_of(host: str) -> Optional[str]:
    for name, cfg in PROGRAMS.items():
        if any(host == d or host.endswith("." + d) for d in cfg["domains"]):
            return name
        if cfg.get("host_re") and re.search(cfg["host_re"], host):
            return name
    return None


def _registered_domain(host: str, network: str) -> str:
    for d in PROGRAMS[network]["domains"]:
        if host == d or host.endswith("." + d):
            return d
    return host


def _first(q: Mapping[str, list], names: Sequence[str]) -> Optional[tuple[str, str]]:
    low = {k.lower(): v for k, v in q.items()}
    for n in names:
        v = (low.get(n) or [""])[0].strip()
        if v:
            return n, v
    return None


def parse_affiliate_url(url: str) -> list[dict]:
    """-> lista rekordów {role, program, network, partner_id|code, merchant, param, domain}. Brak rozpoznania -> []."""
    try:
        u = urlsplit(url.strip())
    except ValueError:
        return []
    host, path, q = (u.hostname or "").lower(), u.path, parse_qs(u.query)
    if not host:
        return []
    out: list[dict] = []
    network = _network_of(host)
    base = {"domain": host}
    if network:
        cfg = PROGRAMS[network]
        pid, param = None, None
        if cfg.get("host_partner") and (m := re.match(cfg["host_partner"], host)):
            pid, param = m.group(1), "host"
            base["merchant"] = m.group(2)
        if not pid and cfg.get("path_partner") and (m := re.search(cfg["path_partner"], path)):
            pid, param = m.group(1), "path"
        if not pid and (hit := _first(q, cfg["partner"])):
            param, pid = hit
        if pid and _ID_OK.match(pid):
            mer = _first(q, cfg["merchant"])
            rec = {"role": "partner_id", "program": network, "network": network, "partner_id": pid, "param": param, **base}
            if mer:
                rec["merchant"] = mer[1]
            out.append(rec)
    else:
        hit = _first(q, GENERIC_PARTNER_PARAMS)
        if hit and _CODE_OK.match(hit[1]):
            # Nieznana sieć: program = domena sprzedawcy (ten sam kod w innym programie to inny klucz).
            out.append({"role": "partner_id", "program": "shop:" + host.removeprefix("www."), "network": None,
                        "partner_id": hit[1], "param": hit[0], **base})
        hit = _first(q, REFERRAL_PARAMS)
        if hit and _CODE_OK.match(hit[1]):
            out.append({"role": "referral_code", "program": "shop:" + host.removeprefix("www."), "network": None,
                        "code": hit[1], "param": hit[0], **base})
    promo = _first(q, PROMO_PARAMS)
    if promo and _CODE_OK.match(promo[1]):
        out.append({"role": "promo_code", "program": ("shop:" + host.removeprefix("www.")) if not network else network,
                    "network": network, "code": promo[1], "param": promo[0], **base})
    return out


def parse_text_codes(text: str) -> list[dict]:
    """Kody z opisów/banerów: [{role, code, label, start, end}]. Kod polecający vs ogólnopromocyjny wg etykiety."""
    out = []
    for m in _TXT.finditer(text):
        code = m.group("code")
        if code.lower() in _STOPWORDS or not (any(c.isdigit() for c in code) or code.isupper()):  # kod != zwykłe słowo
            continue
        role = "referral_code" if _REF_LABEL.search(m.group("lab")) else "promo_code"
        out.append({"role": role, "code": code, "label": m.group("lab").lower(), "start": m.start(), "end": m.end()})
    return out


def _strip_html(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
    return unescape(re.sub(r"<[^>]+>", " ", raw))


def _key(rec: dict) -> str:
    return f"{rec['program']}:{rec.get('partner_id') or rec['code']}"


class AffiliateExtractor:
    spec = AdapterSpec(id="source-affiliate-extractor", kind=AdapterKind.extractor, version=ADAPTERS_VERSION,
                       description="Identyfikator partnera/kod polecający/promo z linków i opisów, z nazwą programu")

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

        def emit(rec, etype, art, source, locator=None):
            refs = [ArtifactRef(artifact_id=art.artifact_id, locator=locator)]
            ev = {"evidence_type": etype, "source": source, "retrieved_at": observation.capture_time, "status": "ok",
                  **{k: v for k, v in rec.items() if v is not None}}
            ev["key"] = _key(rec)
            add("affiliate_evidence", ev, ValueType.json, refs)
            add("affiliate_key", ev["key"] if rec["role"] == "partner_id" else f"{rec['role']}:{ev['key']}",
                ValueType.string, refs)
            if rec.get("network"):
                add("network_domain", {"network": rec["network"], "domain": _registered_domain(rec["domain"], rec["network"])}, ValueType.json, refs)

        relevant = False
        for art in observation.artifacts:
            if art.role is ArtifactRole.input_url:
                relevant = True
                recs = parse_affiliate_url(art.url or "")
                for r in recs:
                    emit(r, "url_param", art, art.url)
                st(ExtractionState.ok, None if recs else "adres bez rozpoznanego kodu partnera/promo", art.artifact_id)
            elif art.role in (ArtifactRole.landing_html, ArtifactRole.document):
                relevant = True
                try:
                    raw = reader.open_bytes(ArtifactRef(artifact_id=art.artifact_id)).decode("utf-8", "replace")
                except Exception as e:  # noqa: BLE001
                    st(ExtractionState.missing_input, f"nie można odczytać bajtów: {e}", art.artifact_id)
                    continue
                n = 0
                is_html = art.role is ArtifactRole.landing_html
                if is_html:
                    for m in _HREF.finditer(raw):
                        for r in parse_affiliate_url(unescape(m.group(1))):
                            n += 1
                            emit(r, "page_link", art, art.url or observation.source_ref, TextSpan(start=m.start(), end=m.end()))
                text = _strip_html(raw) if is_html else raw
                for t in parse_text_codes(text):
                    n += 1
                    rec = {"role": t["role"], "program": "text", "network": None, "code": t["code"], "label": t["label"],
                           "domain": None}
                    emit(rec, "text", art, art.url or observation.source_ref)
                st(ExtractionState.ok, None if n else "brak linków partnerskich i kodów w treści", art.artifact_id)
        if not relevant:
            st(ExtractionState.missing_input, "brak input_url, landing_html ani dokumentu", None)
        return feats, sts


# ---------------------------------------------------------------------------- porównanie

def _evidence(feats: Sequence[Feature]) -> list[tuple[dict, Feature]]:
    return [(f.value, f) for f in feats if f.family == FAMILY and f.kind == "affiliate_evidence" and isinstance(f.value, dict)]


def _refs(items) -> list[ArtifactRef]:
    return [r for _, f in items for r in f.evidence_refs]


class AffiliateComparator:
    method_id = "source-affiliate-compare"
    spec = AdapterSpec(id=method_id, kind=AdapterKind.comparator, version=ADAPTERS_VERSION,
                       description="Wspólny partner w tym samym programie vs kod promo/ten sam kod w innym programie")

    def compare(self, a: Observation, b: Observation, features: Mapping[str, Sequence[Feature]], *, run_id: str,
                optional: Mapping[str, Any] | None = None) -> Sequence[PairEvidence]:
        (ca, oa), (cb, ob) = sorted([(a.canonical_id, a), (b.canonical_id, b)], key=lambda t: t[0])
        ea, eb = _evidence(features.get(oa.observation_id, [])), _evidence(features.get(ob.observation_id, []))

        def make(score, why, refs=None):
            return [PairEvidence(run_id=run_id, pair=(ca, cb), method_id=self.method_id,
                                 relation=Relation.campaign_candidate, score=score, evidence_refs=refs or {},
                                 explanation=why)]

        if not ea or not eb:
            return make(None, "brak podstaw: co najmniej jedna strona nie ma kodu partnera/promo "
                              "(brak kodu to nie dowód różnych partnerów)")
        refs = {ca: _refs(ea), cb: _refs(eb)}

        def ids_of(ev, role):
            return {(e["program"], e.get("partner_id")) for e, _ in ev if e["role"] == role and e.get("partner_id")}

        pa, pb = ids_of(ea, "partner_id"), ids_of(eb, "partner_id")
        shared = sorted(pa & pb)
        if shared:
            prog, pid = shared[0]
            doms = sorted({e["domain"] for e, _ in ea + eb if e.get("domain") and e["program"] == prog})
            via = f" (domeny pośrednika {prog}: {', '.join(doms)})" if len(doms) > 1 and not prog.startswith("shop:") else ""
            return make(1.0, f"ten sam partner {pid} w programie {prog}{via}", refs)
        # ten sam identyfikator w różnych programach: mylące dopasowanie
        same_id = sorted({p for _, p in pa} & {p for _, p in pb})
        if same_id:
            return make(0.2, f"ten sam kod {same_id[0]} w różnych programach "
                             f"({sorted(p for p, _ in pa)[0]} vs {sorted(p for p, _ in pb)[0]}): nie jest to ten sam partner; "
                             "mylące dopasowanie", refs)
        # ten sam pośrednik, różni partnerzy
        na = {e["network"] for e, _ in ea if e.get("network")}
        nb = {e["network"] for e, _ in eb if e.get("network")}
        # wspólny kod polecający/promo
        ca_, cb_ = ({(e["role"], e["code"]) for e, _ in x if e.get("code")} for x in (ea, eb))
        code_a, code_b = {c for _, c in ca_}, {c for _, c in cb_}
        both_codes = sorted(code_a & code_b)
        if both_codes:
            programs_a = {e["program"] for e, _ in ea if e.get("code") == both_codes[0]}
            programs_b = {e["program"] for e, _ in eb if e.get("code") == both_codes[0]}
            roles = {r for r, c in ca_ | cb_ if c == both_codes[0]}
            if "promo_code" in roles:
                return make(0.2, f"wspólny kod {both_codes[0]} ma charakter ogólnopromocyjny (rabatowy), nie identyfikuje "
                                 f"partnera; programy: {sorted(programs_a | programs_b)}", refs)
            if programs_a & programs_b - {"text"}:
                return make(0.6, f"ten sam kod polecający {both_codes[0]} w programie {sorted(programs_a & programs_b)[0]}",
                            refs)
            return make(0.4, f"ten sam kod polecający {both_codes[0]}, ale program nieustalony lub różny "
                             f"({sorted(programs_a | programs_b)})", refs)
        if na & nb:
            return make(0.3, f"ten sam pośrednik {sorted(na & nb)[0]}, ale różni partnerzy: sama sieć nie łączy reklam", refs)
        return make(0.0, "różni partnerzy i kody", refs)
