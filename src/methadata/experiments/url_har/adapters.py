from __future__ import annotations

import json
from typing import Any, Mapping, Optional, Sequence

from methadata.contracts import (AdapterKind, AdapterSpec, ArtifactRef, ExtractionState, ExtractionStatus, Feature,
                                 PairEvidence, Relation, ids)
from methadata.contracts.models import ArtifactRole, HarEntry, ValueType

from . import har as harmod
from .urlparts import GENERIC_PARAMS, MASKED, is_generic_host, parse_url

EXTRACTOR_VERSION = "url-har-1"
FAMILY = "url_har"
METHOD_ID = "url-har-v1"
DEFAULT_WEIGHTS = {"host": 0.2, "path_pattern": 0.2, "param_keys": 0.15, "param_values": 0.15,
                   "host_order": 0.1, "integration_ids": 0.2}
HOST_ONLY_CAP = 0.3  # sam wspólny host nie jest dowodem kampanii


class UrlHarExtractor:
    """URL: host, wzorzec ścieżki, parametry (tylko to, co jest w adresie). HAR: udokumentowane przekierowania 3xx,
    kolejność hostów własnych i identyfikatory integracji z dostawcą. Cecha bez dowodu nie powstaje."""
    spec = AdapterSpec(id="url-har-extractor", kind=AdapterKind.extractor, version=EXTRACTOR_VERSION,
                       description="E04: cechy URL oraz przekierowania i identyfikatory integracji z HAR")

    def _feat(self, oid, kind, vt, value, refs) -> Feature:
        return Feature(feature_id=ids.feature_id(oid, FAMILY, kind, EXTRACTOR_VERSION, value), observation_id=oid,
                       family=FAMILY, kind=kind, value_type=vt, value=value, extractor_version=EXTRACTOR_VERSION,
                       evidence_refs=refs)

    def _st(self, oid, state, detail=None, art=None):
        return ExtractionStatus(observation_id=oid, artifact_id=art, extractor_version=EXTRACTOR_VERSION,
                                state=state, detail=detail)

    def extract(self, observation, *, reader, optional: Mapping[str, Any] | None = None):
        oid = observation.observation_id
        feats: list[Feature] = []
        stats: list[ExtractionStatus] = []
        urls = [a for a in observation.artifacts if a.role is ArtifactRole.input_url and a.url]
        if not urls:
            stats.append(self._st(oid, ExtractionState.missing_input, "brak adresu URL (input_url)"))
        for a in urls:
            parts = parse_url(a.url)
            if parts is None:
                stats.append(self._st(oid, ExtractionState.failed, "adres bez hosta lub niepoprawny", a.artifact_id))
                continue
            feats.append(self._feat(oid, "url_parts", ValueType.json, parts, [ArtifactRef(artifact_id=a.artifact_id)]))
            stats.append(self._st(oid, ExtractionState.ok, None, a.artifact_id))
        hars = [a for a in observation.artifacts if a.role is ArtifactRole.har]
        if not hars:
            stats.append(self._st(oid, ExtractionState.missing_input, "brak HAR: cechy wyłącznie z URL"))
        for a in hars:
            try:
                doc = json.loads(reader.open_bytes(ArtifactRef(artifact_id=a.artifact_id)).decode("utf-8-sig"))
                entries = harmod.har_entries(doc)
            except Exception as ex:  # niezaufany plik
                stats.append(self._st(oid, ExtractionState.failed, f"HAR nieczytelny: {type(ex).__name__}"[:300],
                                      a.artifact_id))
                continue
            ref = lambda i: ArtifactRef(artifact_id=a.artifact_id, locator=HarEntry(index=i))  # noqa: E731
            for ch in harmod.redirect_features(entries):
                feats.append(self._feat(oid, "har_redirect", ValueType.json, ch, [ref(i) for i in ch["indices"]]))
            for d in harmod.integration_features(entries):
                feats.append(self._feat(oid, "integration_id", ValueType.json, d, [ref(d["index"])]))
            order = harmod.first_party_host_order(entries)
            if order:
                feats.append(self._feat(oid, "host_order", ValueType.string_list, [h for h, _ in order],
                                        [ref(i) for _, i in order]))
            stats.append(self._st(oid, ExtractionState.ok, f"{len(entries)} wpisów HAR", a.artifact_id))
        return feats, stats


# ---------------------------------------------------------------- porównanie

def _jaccard(a: set, b: set) -> Optional[float]:
    return len(a & b) / len(a | b) if (a or b) else None


def _lcs(a: Sequence[str], b: Sequence[str]) -> int:
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0]
        for j, y in enumerate(b):
            cur.append(prev[j] + 1 if x == y else max(prev[j + 1], cur[j]))
        prev = cur
    return prev[-1]


def _view(features: Sequence[Feature]) -> dict[str, Any]:
    """Zestaw cech jednej strony do porównania, z odwołaniami dowodowymi na komponent."""
    fs = sorted((f for f in features if f.family == FAMILY), key=lambda f: f.feature_id)
    v: dict[str, Any] = {"url": None, "ids": {}, "order": None, "redirects": [], "refs": {}}
    for f in fs:
        if f.kind == "url_parts" and v["url"] is None:
            v["url"] = f.value
            v["refs"]["url"] = list(f.evidence_refs)
        elif f.kind == "integration_id":
            v["ids"][(f.value["vendor"], f.value["id"])] = f.value
            v["refs"].setdefault("ids", []).extend(f.evidence_refs)
        elif f.kind == "host_order" and v["order"] is None:
            v["order"] = list(f.value)
            v["refs"]["order"] = list(f.evidence_refs)
        elif f.kind == "har_redirect":
            v["redirects"].append(f.value)
            v["refs"].setdefault("redirect", []).extend(f.evidence_refs)
    return v


def _order_seq(v: dict) -> Optional[list[str]]:
    """Kolejność hostów: preferowany udokumentowany łańcuch 3xx (najdłuższy), potem kolejność pierwszych żądań."""
    if v["redirects"]:
        best = max(v["redirects"], key=lambda r: (len(r["hosts"]), r["complete"]))
        return [h for h in best["hosts"] if not is_generic_host(h)] or None
    return v["order"]


def compare_features(fa: Sequence[Feature], fb: Sequence[Feature], weights: Optional[Mapping[str, float]] = None) -> dict:
    """Wynik pochodny: {score, components: {nazwa: {score, detail}}, ignored: [...], used_refs}. Klucze,
    wartości i kolejność hostów liczone osobno; zgodność parametrów/hostów generycznych pomijana."""
    w = dict(weights or DEFAULT_WEIGHTS)
    A, B = _view(fa), _view(fb)
    comp: dict[str, dict] = {}
    ignored: list[str] = []
    ua, ub = A["url"], B["url"]
    ref_kinds: dict[str, set[str]] = {"a": set(), "b": set()}
    if ua and ub:
        ref_kinds["a"].add("url")
        ref_kinds["b"].add("url")
        if is_generic_host(ua["host"]) or is_generic_host(ub["host"]):
            ignored.append("host biblioteki śledzącej pominięty")
        else:
            comp["host"] = {"score": float(ua["host"] == ub["host"]), "detail": f"{ua['host']} | {ub['host']}"}
        if ua["path_pattern"] != "/" or ub["path_pattern"] != "/":
            comp["path_pattern"] = {"score": float(ua["path_pattern"] == ub["path_pattern"]),
                                    "detail": f"{ua['path_pattern']} | {ub['path_pattern']}"}
        ka = {k.lower() for k in ua["param_keys"]}
        kb = {k.lower() for k in ub["param_keys"]}
        shared_generic = sorted((ka & kb) & GENERIC_PARAMS)
        if shared_generic:
            ignored.append("wspólne parametry generyczne pominięte: " + ", ".join(shared_generic))
        sa, sb = ka - GENERIC_PARAMS, kb - GENERIC_PARAMS
        if (j := _jaccard(sa, sb)) is not None:
            comp["param_keys"] = {"score": j, "detail": f"wspólne: {sorted(sa & sb)}; tylko A: {sorted(sa - sb)}; tylko B: {sorted(sb - sa)}"}
        shared = sorted(sa & sb)
        if shared:
            pa = {k.lower(): v for k, v in ua["params"].items()}
            pb = {k.lower(): v for k, v in ub["params"].items()}
            eq = [k for k in shared if sorted(pa[k]) == sorted(pb[k]) and MASKED not in pa[k]]
            comp["param_values"] = {"score": len(eq) / len(shared),
                                    "detail": f"równe wartości: {eq}; różne: {[k for k in shared if k not in eq]}"}
    if A["ids"] or B["ids"]:
        j = _jaccard(set(A["ids"]), set(B["ids"]))
        if j is not None and A["ids"] and B["ids"]:
            both = sorted(set(A["ids"]) & set(B["ids"]))
            comp["integration_ids"] = {"score": j, "detail": "wspólne: " + (", ".join(f"{v}:{i}" for v, i in both) or "brak")}
            ref_kinds["a"].add("ids")
            ref_kinds["b"].add("ids")
    oa, ob = _order_seq(A), _order_seq(B)
    if oa and ob and max(len(oa), len(ob)) >= 2:
        comp["host_order"] = {"score": _lcs(oa, ob) / max(len(oa), len(ob)),
                              "detail": f"{' > '.join(oa)} | {' > '.join(ob)}"}
        key = "redirect" if A["redirects"] and B["redirects"] else "order"
        ref_kinds["a"].add(key)
        ref_kinds["b"].add(key)
    used = {k: w.get(k, 0.0) for k in comp if w.get(k, 0.0) > 0}
    tot = sum(used.values())
    score = sum(comp[k]["score"] * x for k, x in used.items()) / tot if tot else None
    specific = [k for k in used if k != "host" and comp[k]["score"] > 0]
    capped = False
    if score is not None and not specific and score > HOST_ONLY_CAP:
        score, capped = HOST_ONLY_CAP, True
    refs = {s: [r for kk in ("url", "ids", "redirect", "order") if kk in ref_kinds[s] for r in (A if s == "a" else B)["refs"].get(kk, [])]
            for s in "ab"}
    return {"score": score, "components": comp, "ignored": ignored, "weights_used": used, "host_only_capped": capped,
            "refs": refs}


class UrlHarComparator:
    """Porównanie URL/HAR: host, wzorzec ścieżki, klucze, wartości, kolejność hostów i identyfikatory integracji
    liczone osobno i łączone ważoną średnią tylko z dostępnych składników. relation=campaign_candidate."""

    def __init__(self, method_id: str = METHOD_ID, weights: Optional[Mapping[str, float]] = None):
        self.method_id = method_id
        self.weights = dict(weights or DEFAULT_WEIGHTS)
        self.spec = AdapterSpec(id=method_id, kind=AdapterKind.comparator, version="1",
                                description="E04: zgodność kluczy, wartości i kolejności hostów oraz identyfikatorów integracji")

    def compare(self, a, b, features, *, run_id, optional=None) -> Sequence[PairEvidence]:
        (ca, oa), (cb, ob) = sorted([(a.canonical_id, a), (b.canonical_id, b)], key=lambda t: t[0])
        r = compare_features(features.get(oa.observation_id, []), features.get(ob.observation_id, []), self.weights)
        refs = {ca: _dedupe(r["refs"]["a"]), cb: _dedupe(r["refs"]["b"])}
        kw = dict(run_id=run_id, pair=(ca, cb), method_id=self.method_id, relation=Relation.campaign_candidate)
        if r["score"] is None or not refs[ca] or not refs[cb]:
            why = "brak podstaw: żadna z cech URL/HAR nie jest wspólnie dostępna po obu stronach"
            if r["ignored"]:
                why += " (" + "; ".join(r["ignored"]) + ")"
            return [PairEvidence(score=None, explanation=why, **kw)]
        return [PairEvidence(score=min(1.0, max(0.0, r["score"])), evidence_refs=refs,
                             explanation=explain(r), **kw)]


def _dedupe(refs):
    seen, out = set(), []
    for x in refs:
        k = x.model_dump_json()
        if k not in seen:
            seen.add(k)
            out.append(x)
    return out


def explain(r: dict) -> str:
    parts = [f"{k}: {v['score']:.2f} ({v['detail']})" for k, v in sorted(r["components"].items())]
    txt = f"URL/HAR {r['score']:.3f}; wagi {r['weights_used']}. " + "; ".join(parts) + "."
    if r["host_only_capped"]:
        txt += f" Zgodny jest tylko host, wynik ograniczony do {HOST_ONLY_CAP}."
    if r["ignored"]:
        txt += " Pominięto: " + "; ".join(r["ignored"]) + "."
    return txt + " Sam wynik nie przypisuje do kampanii."


def get_adapters() -> list[Any]:
    return [UrlHarExtractor(), UrlHarComparator()]
