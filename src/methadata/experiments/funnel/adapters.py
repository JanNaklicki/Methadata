"""E13: sekwencja działań na stronie (artykuł -> wezwanie -> formularz) odtworzona z HTML i HAR.

Każdy krok ma źródło: `observed` (poświadczone wpisem HAR: załadowanie strony z danym etapem, przekierowanie,
wysłanie formularza) albo `static` (element istnieje w dokumencie, ale jego wystąpienia w przebiegu nie zaobserwowano;
wezwanie do działania jest zawsze `static`, bo kliknięcia nie widać w HAR). Każdy krok ma odnośnik do źródła:
fragment HTML (TextSpan/ByteRange) lub wpis HAR (HarEntry). Sekwencja jest pełna (`complete`) tylko wtedy,
gdy ostatni krok to zaobserwowany formularz albo jego wysłanie; w przeciwnym razie podajemy powód niepełności."""
from __future__ import annotations

import base64
import binascii
import json
from typing import Any, Mapping, Optional, Sequence
from urllib.parse import urlsplit

from methadata.contracts import (AdapterKind, AdapterSpec, ArtifactRef, ExtractionState, ExtractionStatus, Feature,
                                 PairEvidence, Relation)
from methadata.contracts import ids
from methadata.contracts.models import ArtifactRole, ByteRange, HarEntry, TextSpan, ValueType
from methadata.extractors.html.extractor import _decode
from methadata.extractors.html.mhtml import looks_like_mhtml, parse_mhtml

from . import seq, stages

EXTRACTOR_VERSION = "funnel-steps-1"
FAMILY = "funnel"
KIND = "funnel_steps"
MAX_REFS = 20
OBSERVED, STATIC = "observed", "static"
_LANDING_ROLES = (ArtifactRole.landing_dom, ArtifactRole.landing_html)


def _ref_json(r: ArtifactRef) -> dict:
    return r.model_dump(mode="json")


def _host(url: str) -> Optional[str]:
    try:
        return urlsplit(url).hostname
    except ValueError:
        return None


def _body(e: dict) -> Optional[bytes]:
    c = (e.get("response") or {}).get("content") or {}
    t = c.get("text")
    if not isinstance(t, str) or not t:
        return None
    if c.get("encoding") == "base64":
        try:
            return base64.b64decode(t, validate=False)
        except (binascii.Error, ValueError):
            return None
    return t.encode("utf-8")


def _hdr(headers, name):
    for h in headers or []:
        if isinstance(h, dict) and str(h.get("name", "")).lower() == name:
            return str(h.get("value", ""))
    return None


def har_events(entries: list[dict]) -> list[dict]:
    """Zdarzenia nawigacyjne w kolejności wpisów: page (2xx text/html, dokument), redirect (3xx dokumentu),
    submit (POST formularza: form-urlencoded/multipart, odpowiedź 3xx lub text/html). Zasoby, XHR i piksele pomijane."""
    out = []
    for i, e in enumerate(entries):
        req, resp = e.get("request") or {}, e.get("response") or {}
        rtype = e.get("_resourceType")
        status = resp.get("status") if isinstance(resp.get("status"), int) else 0
        mime = ((resp.get("content") or {}).get("mimeType") or "").split(";")[0].strip().lower()
        method = str(req.get("method") or "").upper()
        url = req.get("url") or ""
        if rtype not in (None, "", "document"):
            continue
        if method == "POST":
            pm = (((req.get("postData") or {}).get("mimeType")) or "").split(";")[0].strip().lower()
            if pm in ("application/x-www-form-urlencoded", "multipart/form-data") and (
                    300 <= status < 400 or (200 <= status < 300 and mime == "text/html")):
                out.append({"kind": stages.SUBMIT, "index": i, "detail": f"POST do {_host(url)}{urlsplit(url).path}"})
        elif method == "GET" and 300 <= status < 400 and (resp.get("redirectURL") or _hdr(resp.get("headers"), "location")):
            loc = resp.get("redirectURL") or _hdr(resp.get("headers"), "location") or ""
            out.append({"kind": stages.REDIRECT, "index": i,
                        "detail": f"{status}: {_host(url)} -> {_host(loc) or 'adres względny'}"})
        elif method == "GET" and 200 <= status < 300 and mime == "text/html":
            out.append({"kind": "page", "index": i, "body": _body(e), "detail": f"{_host(url)}{urlsplit(url).path}"})
    return out


def _collapse(steps: list[dict]) -> list[dict]:
    out: list[dict] = []
    for s in steps:
        if out and out[-1]["kind"] == s["kind"]:
            continue
        out.append(s)
    return out


def _incomplete_reason(steps: list[dict]) -> Optional[str]:
    if not steps:
        return "brak jakichkolwiek etapów"
    last = steps[-1]
    if last["kind"] in (stages.FORM, stages.SUBMIT) and last["source"] == OBSERVED:
        return None
    if last["kind"] in (stages.FORM, stages.SUBMIT):
        return f"ostatni etap „{last['kind']}” istnieje tylko w statycznym dokumencie (nie zaobserwowano)"
    return f"nie zaobserwowano dalszego kroku po „{last['kind']}” (formularz/wysłanie)"


class FunnelExtractor:
    spec = AdapterSpec(id="funnel-extractor", kind=AdapterKind.extractor, version=EXTRACTOR_VERSION,
                       description="Sekwencja etapów strony (artykuł, wezwanie, formularz, przekierowanie, wysłanie) "
                                   "z HTML i HAR; każdy krok: zaobserwowany lub statyczny, z odnośnikiem do źródła")

    def extract(self, observation, *, reader, optional: Mapping[str, Any] | None = None):
        oid = observation.observation_id
        stats: list[ExtractionStatus] = []

        def st(state, detail, art=None):
            stats.append(ExtractionStatus(observation_id=oid, artifact_id=art, extractor_version=EXTRACTOR_VERSION,
                                          state=state, detail=detail))

        html_art = next((a for r in _LANDING_ROLES for a in observation.artifacts if a.role is r), None)
        har_art = next((a for a in observation.artifacts if a.role is ArtifactRole.har), None)
        if html_art is None and har_art is None:
            st(ExtractionState.missing_input, "brak artefaktu landing_dom/landing_html oraz HAR")
            return [], stats

        html_stages: list[stages.Stage] = []
        html_loc = None
        if html_art is not None:
            try:
                raw = reader.open_bytes(ArtifactRef(artifact_id=html_art.artifact_id))
                if looks_like_mhtml(raw, html_art.media_type, html_art.origin or html_art.local_ref):
                    main = parse_mhtml(raw).main_part()
                    if main is None:
                        raise ValueError("MHTML bez części text/html")
                    text, _ = _decode(main.data, main.charset)
                    html_loc = ByteRange(start=main.byte_start, end=main.byte_end)
                else:
                    text, _ = _decode(raw, None)
                html_stages = stages.analyze_html(text)
            except Exception as e:
                st(ExtractionState.failed, f"HTML nieczytelny: {type(e).__name__}: {e}"[:300], html_art.artifact_id)
                html_art = None

        events: list[dict] = []
        if har_art is not None:
            try:
                doc = json.loads(reader.open_bytes(ArtifactRef(artifact_id=har_art.artifact_id)).decode("utf-8-sig"))
                entries = doc["log"]["entries"]
                if not isinstance(entries, list) or not all(isinstance(x, dict) for x in entries):
                    raise ValueError("log.entries nie jest listą obiektów")
                events = har_events(entries)
            except Exception as e:
                st(ExtractionState.failed, f"HAR nieczytelny: {type(e).__name__}: {e}"[:300], har_art.artifact_id)
                har_art = None

        if html_art is None and har_art is None:  # wszystkie źródła nieczytelne (statusy failed już zapisane)
            return [], stats

        def har_ref(i):
            return ArtifactRef(artifact_id=har_art.artifact_id, locator=HarEntry(index=i))

        def html_ref(s: stages.Stage):
            return ArtifactRef(artifact_id=html_art.artifact_id,
                               locator=html_loc or TextSpan(start=s.start, end=s.end))

        def step(kind, source, ref, detail, observed_ref=None):
            d = {"kind": kind, "source": source, "ref": _ref_json(ref), "detail": detail}
            if observed_ref is not None:
                d["observed_ref"] = _ref_json(observed_ref)
            return d

        steps: list[dict] = []
        notes: list[str] = []
        first_page = next((e for e in events if e["kind"] == "page"), None)
        if html_art is not None and first_page is None:  # sam dokument: wszystko statyczne
            steps += [step(s.kind, STATIC, html_ref(s), s.detail) for s in html_stages]
        for ev in events:
            if ev["kind"] != "page":
                steps.append(step(ev["kind"], OBSERVED, har_ref(ev["index"]), ev["detail"]))
                continue
            if ev is first_page and html_art is not None:
                for s in html_stages:
                    if s.kind == stages.CTA:
                        steps.append(step(s.kind, STATIC, html_ref(s), s.detail))
                    else:
                        steps.append(step(s.kind, OBSERVED, html_ref(s), s.detail, har_ref(ev["index"])))
                continue
            if ev.get("body") is None:
                notes.append(f"strona z wpisu {ev['index']} bez treści odpowiedzi (etapów nie rozpoznano)")
                continue
            try:
                text, _ = _decode(ev["body"], None)
                page_stages = stages.analyze_html(text)
            except Exception as e:
                notes.append(f"wpis {ev['index']}: {type(e).__name__}")
                continue
            for s in page_stages:
                steps.append(step(s.kind, STATIC if s.kind == stages.CTA else OBSERVED, har_ref(ev["index"]), s.detail))
        steps = _collapse(steps)
        reason = _incomplete_reason(steps)
        value = {"steps": steps, "complete": reason is None, "incomplete_reason": reason,
                 "observed": sum(s["source"] == OBSERVED for s in steps),
                 "static": sum(s["source"] == STATIC for s in steps),
                 "capture": "har+html" if (har_art and html_art) else "har" if har_art else "html"}
        refs = []
        for s in steps:
            r = ArtifactRef.model_validate(s["ref"])
            if r not in refs:
                refs.append(r)
        f = Feature(feature_id=ids.feature_id(oid, FAMILY, KIND, EXTRACTOR_VERSION, value), observation_id=oid,
                    family=FAMILY, kind=KIND, value_type=ValueType.json, value=value,
                    extractor_version=EXTRACTOR_VERSION, evidence_refs=refs[:MAX_REFS * 2])
        st(ExtractionState.ok, f"kroków={len(steps)}, zaobserwowanych={value['observed']}"
           + ("" if reason is None else f"; niepełna: {reason}") + "".join("; " + n for n in notes),
           (har_art or html_art).artifact_id)
        return [f], stats


def _load(features: Sequence[Feature]):
    for f in features:
        if f.family == FAMILY and f.kind == KIND and isinstance(f.value, dict) and f.value.get("steps"):
            return f
    return None


class FunnelComparator:
    """Porównanie kolejności etapów. metric: `edit` (1 - odległość edycyjna / max długość) lub `lcs` (najdłuższy wspólny
    podciąg / max długość). mode: `full` (całe sekwencje) lub `truncated` (oba przebiegi ucięte do długości krótszego,
    czyli porównanie wspólnego prefiksu; brak obserwacji dalszych kroków nie obniża wyniku)."""

    def __init__(self, metric: str = "edit", mode: str = "full"):
        assert metric in ("edit", "lcs") and mode in ("full", "truncated")
        self.metric, self.mode = metric, mode
        self.method_id = f"funnel-{metric}-{mode}-v1"
        self.spec = AdapterSpec(
            id=self.method_id, kind=AdapterKind.comparator, version="1",
            description=("Kolejność etapów strony, " + ("odległość edycyjna" if metric == "edit" else "najdłuższy wspólny podciąg")
                         + (", pełne sekwencje" if mode == "full" else ", sekwencje ucięte do wspólnego prefiksu")))

    def compare(self, a, b, features, *, run_id, optional=None) -> Sequence[PairEvidence]:
        (ca, fa), (cb, fb) = sorted([(a.canonical_id, _load(features.get(a.observation_id, []))),
                                     (b.canonical_id, _load(features.get(b.observation_id, [])))], key=lambda t: t[0])
        pair = (ca, cb)

        def none(why):
            return [PairEvidence(run_id=run_id, pair=pair, method_id=self.method_id, relation=Relation.shared_template,
                                 score=None, explanation="brak podstaw: " + why)]

        if fa is None or fb is None:
            return none("brak cechy sekwencji etapów po jednej ze stron lub po obu")
        sa, sb = fa.value["steps"], fb.value["steps"]
        if self.mode == "truncated":
            k = min(len(sa), len(sb))
            sa, sb = sa[:k], sb[:k]
        ka, kb = [s["kind"] for s in sa], [s["kind"] for s in sb]
        score = (seq.edit_similarity if self.metric == "edit" else seq.lcs_similarity)(ka, kb)
        if score is None:
            return none("pusta sekwencja etapów")
        pairs = seq.lcs_pairs(ka, kb)
        matched = {ca: {i for i, _ in pairs}, cb: {j for _, j in pairs}}
        refs: dict[str, list[ArtifactRef]] = {}
        for cid, steps in ((ca, sa), (cb, sb)):
            # najpierw dopasowane etapy, potem pozostałe (każdy etap ma odnośnik do źródła)
            order = sorted(range(len(steps)), key=lambda i: (i not in matched[cid], i))
            lst: list[ArtifactRef] = []
            for i in order:
                r = ArtifactRef.model_validate(steps[i]["ref"])
                if r not in lst:
                    lst.append(r)
            refs[cid] = lst[:MAX_REFS]

        def desc(cid, f, steps):
            v = f.value
            tag = "pełna" if v["complete"] else f"niepełna ({v['incomplete_reason']})"
            marks = ">".join(f"{s['kind']}{'*' if s['source'] == STATIC else ''}" for s in steps)
            return f"{cid}: {marks} [{tag}]"
        d = seq.levenshtein(ka, kb)
        expl = (f"{'odległość edycyjna' if self.metric == 'edit' else 'wspólny podciąg'} etapów, wynik {score:.4f} "
                f"(edycja {d}, wspólny podciąg {len(pairs)}, długości {len(ka)}/{len(kb)}, tryb "
                f"{'pełny' if self.mode == 'full' else 'ucięty do wspólnego prefiksu'}). "
                f"{desc(ca, fa, sa)}; {desc(cb, fb, sb)}. Gwiazdka = etap tylko w statycznym dokumencie.")
        return [PairEvidence(run_id=run_id, pair=pair, method_id=self.method_id, relation=Relation.shared_template,
                             score=score, evidence_refs=refs, explanation=expl)]


def get_adapters() -> list[Any]:
    return [FunnelExtractor()] + [FunnelComparator(m, md) for m in ("edit", "lcs") for md in ("full", "truncated")]
