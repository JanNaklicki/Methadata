import json
from pathlib import Path

import pytest

from methadata.contracts import AdapterKind, Artifact, InputKind, Observation, ids
from methadata.contracts.models import HarEntry
from methadata.contracts.registry import discover
from methadata.experiments.url_har import (UrlHarComparator, UrlHarExtractor, compare_features, detect_integrations,
                                           parse_url, redirect_features)

BACKUP = Path("/Users/jnaklicki/Documents/Hackathon/Methadata/BACKUP")


class Reader:
    def __init__(self): self.d = {}
    def open_bytes(self, ref): return self.d[ref.artifact_id]


def url_obs(name, url):
    oid = ids.observation_id(name, "url_only")
    sha = ids.sha256_bytes(url.encode())
    a = Artifact(artifact_id=ids.artifact_id(oid, "input_url", sha, "url"), observation_id=oid, role="input_url",
                 sha256=sha, url=url)
    return Observation(observation_id=oid, canonical_id="can-" + name, source_ref=name,
                       input_kind=InputKind.url_only, artifacts=[a])


def har_obs(name, url, entries, reader):
    oid = ids.observation_id(name, "full_capture")
    data = json.dumps({"log": {"entries": entries}}).encode()
    sha = ids.sha256_bytes(data)
    h = Artifact(artifact_id=ids.artifact_id(oid, "har", sha), observation_id=oid, role="har",
                 local_ref=f"{name}.har", sha256=sha, media_type="application/json")
    reader.d[h.artifact_id] = data
    usha = ids.sha256_bytes(url.encode())
    u = Artifact(artifact_id=ids.artifact_id(oid, "input_url", usha, "url"), observation_id=oid, role="input_url",
                 sha256=usha, url=url)
    return Observation(observation_id=oid, canonical_id="can-" + name, source_ref=name,
                       input_kind=InputKind.full_capture, artifacts=[h, u])


def ent(url, status=200, loc=None, t="2026-01-01T00:00:00.000Z"):
    return {"startedDateTime": t, "request": {"method": "GET", "url": url},
            "response": {"status": status, "redirectURL": loc or "", "headers": [], "content": {}}}


def feats(o, reader=None):
    f, st = UrlHarExtractor().extract(o, reader=reader or Reader())
    return list(f), st


def cmp(a, b, fa, fb):
    return UrlHarComparator().compare(a, b, {a.observation_id: fa, b.observation_id: fb}, run_id="r")[0]


def test_parse_url_parts():
    p = parse_url("https://Shop.Example.com/offer/12345/ab?x=1&gclid=SECRET&x=2")
    assert p["host"] == "shop.example.com" and p["path_pattern"] == "/offer/{int}/ab"
    assert p["param_keys"] == ["x", "gclid"] and p["params"]["gclid"] == ["***"] and p["params"]["x"] == ["1", "2"]
    assert parse_url("nie-url") is None


def test_three_url_only_get_url_evidence():
    urls = {"a": "https://promo.example/zloto/123?camp=kz&src=fb&gad_source=1",
            "b": "https://promo.example/zloto/456?camp=kz&src=tt&gad_source=1",
            "c": "https://other.example/krem?camp=kr"}
    obs = {k: url_obs(k, u) for k, u in urls.items()}
    fs = {k: feats(o)[0] for k, o in obs.items()}
    assert all(f and f[0].evidence_refs and f[0].kind == "url_parts" for f in fs.values())
    ab, ac = cmp(obs["a"], obs["b"], fs["a"], fs["b"]), cmp(obs["a"], obs["c"], fs["a"], fs["c"])
    assert ab.score is not None and ab.evidence_refs["can-a"] and ab.evidence_refs["can-b"]
    assert ab.score > ac.score and ab.relation.value == "campaign_candidate"
    assert "param_keys" in ab.explanation and "param_values" in ab.explanation
    assert cmp(obs["a"], obs["b"], fs["a"], fs["b"]) == ab


def test_keys_values_separate():
    a, b = url_obs("a", "https://x.example/p?k1=1&k2=2"), url_obs("b", "https://x.example/p?k1=9&k2=8")
    r = compare_features(feats(a)[0], feats(b)[0])
    assert r["components"]["param_keys"]["score"] == 1.0 and r["components"]["param_values"]["score"] == 0.0


def test_shared_gad_source_alone_is_not_campaign():
    a, b = url_obs("a", "https://one.example/?gad_source=1"), url_obs("b", "https://two.example/?gad_source=1")
    ev = cmp(a, b, feats(a)[0], feats(b)[0])
    assert ev.score == 0.0 and "gad_source" in ev.explanation and "pominięt" in ev.explanation
    # sam host + wspólne generyki nie daje wysokiego wyniku
    c, d = url_obs("c", "https://same.example/?gad_source=1"), url_obs("d", "https://same.example/?gad_source=1")
    assert cmp(c, d, feats(c)[0], feats(d)[0]).score <= 0.3


def test_tracking_library_alone_does_not_match():
    r = Reader()
    lib = [ent("https://www.googletagmanager.com/gtag/js"), ent("https://connect.facebook.net/en_US/fbevents.js")]
    a = har_obs("a", "https://one.example/", [ent("https://one.example/")] + lib, r)
    b = har_obs("b", "https://two.example/", [ent("https://two.example/")] + lib, r)
    fa, fb = feats(a, r)[0], feats(b, r)[0]
    assert not [f for f in fa if f.kind == "integration_id"]
    assert cmp(a, b, fa, fb).score == 0.0


def test_request_list_is_not_redirect_chain_and_3xx_is():
    r = Reader()
    plain = [ent("https://a.example/"), ent("https://b.example/x", t="2026-01-01T00:00:01.000Z"),
             ent("https://c.example/y", t="2026-01-01T00:00:02.000Z")]
    assert redirect_features(plain) == []
    o = har_obs("p", "https://a.example/", plain, r)
    assert not [f for f in feats(o, r)[0] if f.kind == "har_redirect"]
    red = [ent("https://t.example/click?gclid=ZZZ", 302, "https://lp.example/offer"),
           ent("https://lp.example/offer", t="2026-01-01T00:00:01.000Z")]
    o2 = har_obs("r", "https://t.example/click", red, r)
    f = [x for x in feats(o2, r)[0] if x.kind == "har_redirect"]
    assert len(f) == 1 and f[0].value["hosts"] == ["t.example", "lp.example"] and f[0].value["complete"]
    assert [x.locator for x in f[0].evidence_refs] == [HarEntry(index=0), HarEntry(index=1)]
    assert "ZZZ" not in json.dumps(f[0].value)


def test_integration_ids_with_vendor_context_and_match():
    assert detect_integrations("https://www.facebook.com/tr/?id=4567646436826205&ev=PageView")[0] == {
        "vendor": "meta_pixel", "id_type": "pixel", "id": "4567646436826205", "host": "www.facebook.com"}
    assert detect_integrations("https://www.googletagmanager.com/gtm.js?id=GTM-ABC123")[0]["vendor"] == "google_tag_manager"
    assert detect_integrations("https://connect.facebook.net/en_US/fbevents.js") == []
    r = Reader()
    px = "https://www.facebook.com/tr/?id=111222333&ev=PageView"
    a = har_obs("a", "https://one.example/p", [ent("https://one.example/p"), ent(px)], r)
    b = har_obs("b", "https://two.example/p", [ent("https://two.example/p"), ent(px)], r)
    fa, fb = feats(a, r)[0], feats(b, r)[0]
    idf = [f for f in fa if f.kind == "integration_id"][0]
    assert idf.evidence_refs[0].locator == HarEntry(index=1) and idf.value["vendor"] == "meta_pixel"
    ev = cmp(a, b, fa, fb)
    assert ev.score > 0.3 and "meta_pixel:111222333" in ev.explanation
    assert any(isinstance(x.locator, HarEntry) for x in ev.evidence_refs["can-a"])


def test_host_order_separate():
    def chain(*hosts):
        e = [ent(f"https://{h}/s", 302, f"https://{hosts[i+1]}/s", t=f"2026-01-01T00:00:0{i}.000Z")
             for i, h in enumerate(hosts[:-1])]
        return e + [ent(f"https://{hosts[-1]}/s", t="2026-01-01T00:00:09.000Z")]
    r = Reader()
    a = har_obs("a", "https://h1.example/s", chain("h1.example", "h2.example", "h3.example"), r)
    same = har_obs("b", "https://h1.example/s", chain("h1.example", "h2.example", "h3.example"), r)
    rev = har_obs("c", "https://h1.example/s", chain("h3.example", "h2.example", "h1.example"), r)
    fa, fs, fr = (feats(o, r)[0] for o in (a, same, rev))
    ro = compare_features(fa, fs)["components"]["host_order"]["score"]
    rr = compare_features(fa, fr)["components"]["host_order"]["score"]
    assert ro == 1.0 and rr < 1.0
    assert "host" in compare_features(fa, fr)["components"]  # zgodność zbioru hostów osobno od kolejności


def test_missing_and_broken_inputs():
    r = Reader()
    o = har_obs("a", "https://a.example/", [], r)
    r.d[next(a.artifact_id for a in o.artifacts if a.role.value == "har")] = b"{nie json"
    f, st = feats(o, r)
    assert [x for x in st if x.state.value == "failed"] and f[0].kind == "url_parts"
    a, b = url_obs("a", "https://a.example/"), url_obs("b", "https://b.example/")
    assert cmp(a, b, [], []).score is None


def test_discover():
    found = discover()
    assert found["url-har-extractor"].spec.kind is AdapterKind.extractor
    assert found["url-har-v1"].spec.kind is AdapterKind.comparator


def test_runner_integration():
    from methadata.runner.run import run_experiment
    obs = [url_obs("a", "https://p.example/z/1?camp=k"), url_obs("b", "https://p.example/z/2?camp=k"),
           url_obs("c", "https://q.example/o?m=1")]
    ad = discover()
    res = run_experiment({"dataset_version": "t", "seed": 0, "relation": "campaign_candidate",
                          "extractors": ["url-har-extractor"], "comparators": ["url-har-v1"],
                          "clusterer": kind_cl_id(ad),
                          "cluster_config": {"threshold": 0.7, "similarity_version": "url-har-v1", "dataset_version": "t",
                                             "method_id": "url-har-v1"}}, obs, ad, reader=Reader())
    ab = [p for p in res["bundle"].pairs if p.pair == ("can-a", "can-b")][0]
    assert ab.score >= 0.7 and res["bundle"].pairs and all(p.method_id == "url-har-v1" for p in res["bundle"].pairs)


def kind_cl(v):
    return v.spec.kind is AdapterKind.clusterer


def kind_cl_id(ad):
    return next(k for k, v in ad.items() if kind_cl(v))


@pytest.mark.skipif(not BACKUP.is_dir(), reason="brak katalogu BACKUP")
def test_real_har_pixel_ids():
    from methadata.experiments.url_har.har import har_entries, integration_features
    found = 0
    for p in sorted(BACKUP.glob("*/*.har")):
        try:
            found += len(integration_features(har_entries(json.loads(p.read_text("utf-8-sig")))))
        except (ValueError, KeyError, json.JSONDecodeError):
            continue
    assert found >= 1
