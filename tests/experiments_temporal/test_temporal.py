import json
from pathlib import Path

import pytest

from methadata.contracts import Artifact, ArtifactRef, Feature, InputKind, Observation, ValueType, ids
from methadata.contracts.registry import discover
from methadata.experiments.temporal import (TemporalComparator, build_interval, compare_models, detect_series,
                                            make_item, pair_model)


def mk(name, start, stop=None, cap="2026-11-01T10:00:00Z", domain=None, img=None):
    oid = ids.observation_id(name, "full_capture")
    art = Artifact(artifact_id=ids.artifact_id(oid, "document", "a" * 64), observation_id=oid, role="document",
                   local_ref=f"{name}.json", sha256="a" * 64, media_type="application/json")
    obs = Observation(observation_id=oid, canonical_id="can-" + name, source_ref=name,
                      input_kind=InputKind.full_capture, artifacts=[art], capture_time=cap)
    ref = [ArtifactRef(artifact_id=art.artifact_id)]
    fs = []

    def add(fam, kind, v):
        fs.append(Feature(feature_id=ids.feature_id(oid, fam, kind, "t/1", v), observation_id=oid, family=fam, kind=kind,
                          value_type=ValueType.json if isinstance(v, dict) else ValueType.string, value=v, extractor_version="t/1", evidence_refs=ref))
    if start:
        add("payer", "delivery_start", start)
    if stop:
        add("payer", "delivery_stop", stop)
    if domain:
        add("url_har", "url_parts", {"host": domain})
    for i in ([img] if isinstance(img, str) else img or []):
        add("asset", "image_sha256", i)
    return obs, fs


def corpus(*specs):
    obs, fm = [], {}
    for s in specs:
        o, f = mk(**s)
        obs.append(o)
        fm[o.observation_id] = f
    return obs, fm


def items(obs, fm, cfg=None):
    return [make_item(o, fm[o.observation_id], cfg)[0] for o in obs]


def test_discover_and_timezone():
    assert "temporal-emission-v1" in set(discover())
    iv, _ = build_interval("2026-03-28", "2026-03-29", None)  # zmiana czasu w Polsce: doba 29.03 ma 23 h
    assert iv.start.isoformat() == "2026-03-27T23:00:00+00:00" and iv.end.isoformat() == "2026-03-29T22:00:00+00:00"
    iv2, _ = build_interval("2026-01-10T12:00:00+02:00", "2026-01-11", None, "UTC")
    assert iv2.start.hour == 10 and iv2.end.isoformat() == "2026-01-12T00:00:00+00:00"


def test_unreliable_dates():
    assert build_interval(None, None, "2026-11-01T10:00:00Z")[0] is None
    assert build_interval("2026-12-01", None, "2026-11-01T10:00:00Z")[0] is None   # start po obserwacji
    assert build_interval("2026-10-05", "2026-10-01", None)[0] is None
    assert build_interval("2026-10-05", None, None)[0] is None                     # okres otwarty bez obserwacji
    iv, _ = build_interval("2026-10-05", None, "2026-11-01T10:00:00Z")
    assert iv.censored and iv.end.isoformat().startswith("2026-11-01T10")          # zrzut = tylko granica obserwacji


def test_series_sequential_and_time_model_cuts_old_reuse():
    obs, fm = corpus(
        dict(name="a1", start="2026-09-01", stop="2026-09-10", domain="a.pl", img="H"),
        dict(name="a2", start="2026-09-08", stop="2026-09-20", domain="b.pl", img="H"),
        dict(name="a3", start="2026-09-15", stop="2026-09-30", domain="c.pl", img="H"),
        dict(name="old", start="2025-01-01", stop="2025-01-31", domain="d.pl", img="H"),   # ten sam obraz rok wcześniej
        dict(name="x", start="2026-09-02", stop="2026-09-12", domain="z.pl", img="Z"))
    its = items(obs, fm)
    [s] = detect_series(its)
    assert s.kind == "sequential" and [m.domain for m in s.members] == ["a.pl", "b.pl", "c.pl"]
    truth = {("can-a1", "can-a2"): True, ("can-a1", "can-a3"): True, ("can-a2", "can-a3"): True,
             ("can-old", "can-a1"): False, ("can-old", "can-a2"): False, ("can-old", "can-a3"): False}
    rep = compare_models(its, truth=truth)
    assert rep["pairs_no_time"] == 6 and rep["pairs_time"] == 3 and len(rep["pairs_cut_by_time"]) == 3
    assert rep["no_time"]["precision"] == 0.5 and rep["with_time"]["precision"] == 1.0
    assert rep["with_time"]["recall"] == rep["no_time"]["recall"] == 1.0


def test_coactive_pair_without_shared_trace_is_capped():
    obs, fm = corpus(dict(name="p", start="2026-09-01", stop="2026-09-30", domain="a.pl", img="H1"),
                     dict(name="q", start="2026-09-05", stop="2026-09-25", domain="b.pl", img="H2"))
    [pe] = TemporalComparator().compare(obs[0], obs[1], fm, run_id="r1")
    assert pe.score == pytest.approx(0.2) and "bez wspólnego wzorca" in pe.explanation and "Europe/Warsaw" in pe.explanation
    assert pe.evidence_refs and pe.relation.value == "campaign_candidate"
    its = items(obs, fm)
    assert pair_model(its[0], its[1])["shared"] == []


def test_shared_pattern_close_vs_far_and_none():
    obs, fm = corpus(dict(name="a", start="2026-09-01", stop="2026-09-10", domain="a.pl", img="H"),
                     dict(name="b", start="2026-09-05", stop="2026-09-12", domain="b.pl", img="H"),
                     dict(name="c", start="2025-01-01", stop="2025-01-10", domain="c.pl", img="H"),
                     dict(name="n", start=None, domain="d.pl", img="H"))
    cmp_ = TemporalComparator()
    close = cmp_.compare(obs[0], obs[1], fm, run_id="r")[0]
    far = cmp_.compare(obs[0], obs[2], fm, run_id="r")[0]
    missing = cmp_.compare(obs[0], obs[3], fm, run_id="r")[0]
    assert close.score == 1.0 and 0.5 <= far.score < 0.51 and missing.score is None
    assert "delivery_start" in missing.explanation


def test_run_experiment_integration():
    from methadata.runner import run_experiment
    assert callable(run_experiment)  # pełny przebieg wymaga konfiguracji F03; komparator spełnia kontrakt Comparator
    from methadata.contracts.interfaces import check_adapter
    assert check_adapter(TemporalComparator()).id == "temporal-emission-v1"


def test_config_file_matches_defaults():
    from methadata.experiments.temporal import DEFAULT_CONFIG
    p = Path(__file__).resolve().parents[2] / "configs" / "temporal" / "temporal.json"
    c = json.loads(p.read_text())
    assert all(c[k] == DEFAULT_CONFIG[k] for k in DEFAULT_CONFIG)


def test_registrable_domain():
    from methadata.experiments.temporal.series import host_of, registrable_domain
    assert registrable_domain("sub.sklep.example.com") == "example.com"
    assert registrable_domain("a.sklep.com.pl") == "sklep.com.pl" and registrable_domain("x.shop.co.uk") == "shop.co.uk"
    assert registrable_domain("1.2.3.4") == "1.2.3.4" and registrable_domain("localhost") == "localhost"
    assert host_of({"host": "www.promo.example.pl", "path": "/"}) == "example.pl"


def test_integration_real_extractors_series_on_three_domains():
    from methadata.experiments.url_har import UrlHarExtractor
    from methadata.sources.payer import PayerExtractor

    class Mem:
        def __init__(self): self.d = {}
        def open_bytes(self, ref): return self.d[ref.artifact_id]
    mem = Mem()
    obs, fm = [], {}
    for n, (url, start, stop) in enumerate([("https://www.oferta.alfa.pl/a?x=1", "2026-09-01", "2026-09-10"),
                                            ("https://promo.beta.com.pl/b", "2026-09-07", "2026-09-20"),
                                            ("https://gamma.example.com/c", "2026-09-14", "2026-09-30"),
                                            ("https://delta.pl/d", "2025-01-01", "2025-01-10")]):
        data = json.dumps({"id": str(n), "page_name": "Marka", "ad_delivery_start_time": start,
                           "ad_delivery_stop_time": stop,
                           "beneficiary_payers": [{"payer": "Firma X", "beneficiary": "Firma X"}]}).encode()
        oid = ids.observation_id(f"synthetic://i{n}", "full_capture")
        sha = ids.sha256_bytes(data)
        doc = Artifact(artifact_id=ids.artifact_id(oid, "document", sha, "r.json"), observation_id=oid, role="document",
                       local_ref=f"i{n}/r.json", sha256=sha, media_type="application/json", origin="r.json")
        inp = Artifact(artifact_id=ids.artifact_id(oid, "input_url", ids.sha256_bytes(url.encode())), observation_id=oid,
                       role="input_url", url=url, sha256=ids.sha256_bytes(url.encode()))
        mem.d[doc.artifact_id] = data
        o = Observation(observation_id=oid, canonical_id=f"can-i{n}", source_ref=f"synthetic://i{n}",
                        input_kind=InputKind.creative_only, artifacts=[doc, inp], tags=["synthetic"],
                        capture_time="2026-11-01T10:00:00Z")
        obs.append(o)
        fm[oid] = list(PayerExtractor().extract(o, reader=mem)[0]) + list(UrlHarExtractor().extract(o, reader=mem)[0])
    cfg = {"pattern_kinds": [["payer", "payer_name"]], "max_gap_days": 14}
    its = items(obs, fm, cfg)
    assert [i.domain for i in its] == ["alfa.pl", "beta.com.pl", "example.com", "delta.pl"]
    [s] = detect_series(its, cfg)   # bez delta.pl (rok wcześniej, poza oknem odstępu)
    assert s.kind == "sequential" and [m.domain for m in s.members] == ["alfa.pl", "beta.com.pl", "example.com"]
    [pe] = TemporalComparator(cfg).compare(obs[0], obs[1], fm, run_id="r1")
    assert pe.score == 1.0
