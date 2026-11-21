from pathlib import Path

import pytest

from methadata.contracts import ids
from methadata.contracts.interfaces import AdapterKind
from methadata.contracts.models import Artifact, ArtifactRole, Bundle, ExtractionState, InputKind, Observation
from methadata.contracts.registry import discover
from methadata.sources.mobile_app import MobileAppComparator, MobileAppExtractor, parse_assetlinks, parse_store_url

S = Path(__file__).parent / "samples"
PLAY = "https://play.google.com/store/apps/details?id=pl.example.bank&hl=pl"


class Mem:
    def __init__(self):
        self.d = {}

    def open_bytes(self, ref):
        return self.d[ref.artifact_id]


def obs(name, mem, *, url=None, html=None, assetlinks=None, al_url=None):
    oid = ids.observation_id("synthetic://" + name, "full_capture")
    arts = []

    def add(role, data=None, **kw):
        sha = ids.sha256_bytes(data if data is not None else kw["url"].encode())
        a = Artifact(artifact_id=ids.artifact_id(oid, role.value, sha, name + role.value), observation_id=oid,
                     role=role, sha256=sha, **kw)
        if data is not None:
            mem.d[a.artifact_id] = data
        arts.append(a)

    if url:
        add(ArtifactRole.input_url, url=url)
    if html:
        add(ArtifactRole.landing_html, (S / html).read_bytes(), local_ref="x/l.html", media_type="text/html")
    if assetlinks:
        add(ArtifactRole.document, (S / assetlinks).read_bytes(), local_ref="x/assetlinks.json",
            media_type="application/json", origin="assetlinks.json", url=al_url)
    return Observation(
        observation_id=oid, canonical_id=ids.canonical_id([], url="synthetic://" + name), source_ref="synthetic://" + name,
        tags=["synthetic"], capture_time="2026-11-01T10:00:00Z",
        input_kind=InputKind.url_only if url and not html and not assetlinks else InputKind.full_capture, artifacts=arts)


def run(*observations, mem):
    ex, feats = MobileAppExtractor(), {}
    for o in observations:
        f, s = ex.extract(o, reader=mem)
        feats[o.observation_id] = f
        assert all(x.state is ExtractionState.ok for x in s), s
    return feats


def ev(feats, oid):
    return [f.value for f in feats[oid] if f.kind == "link_evidence"]


def cmp(a, b, feats):
    [pe] = MobileAppComparator().compare(a, b, feats, run_id="r1")
    return pe


def test_registered():
    assert {"source-mobile-app-extractor", "source-mobile-app-compare"} <= set(discover())
    assert discover(AdapterKind.comparator)["source-mobile-app-compare"].spec.kind is AdapterKind.comparator


def test_parse_store_urls():
    assert parse_store_url(PLAY)["app_id"] == "pl.example.bank"
    assert parse_store_url("market://details?id=pl.example.bank")["store"] == "google_play"
    a = parse_store_url("https://apps.apple.com/pl/app/moj-bank/id1234567890?l=pl")
    assert (a["store"], a["app_id"]) == ("apple_app_store", "1234567890")
    assert parse_store_url("https://appgallery.huawei.com/app/C100123456")["app_id"] == "C100123456"
    assert parse_store_url("https://galaxystore.samsung.com/detail/pl.example.bank")["store"] == "samsung_galaxy_store"
    assert parse_store_url("https://example.com/store/apps/details?id=pl.example.bank") is None
    assert parse_store_url("https://play.google.com/store/apps/details?id=../x") is None


def test_parse_assetlinks_only_handle_all_urls():
    doc = [{"relation": ["delegate_permission/common.get_login_creds"],
            "target": {"namespace": "android_app", "package_name": "a.b", "sha256_cert_fingerprints": ["AA"]}},
           {"relation": ["delegate_permission/common.handle_all_urls"],
            "target": {"namespace": "web", "site": "https://x.pl"}}]
    assert parse_assetlinks(doc) == [] and parse_assetlinks({}) == []


def test_store_url_and_assetlinks_features_valid():
    mem = Mem()
    o = obs("a", mem, url=PLAY, assetlinks="assetlinks_a.json", al_url="https://bank-a.example/.well-known/assetlinks.json")
    f = run(o, mem=mem)[o.observation_id]
    kinds = {(x.kind, x.value if isinstance(x.value, str) else x.value.get("evidence_type")) for x in f}
    assert ("store_name", "google_play") in kinds and ("app_id", "pl.example.bank") in kinds
    assert ("link_evidence", "store_url") in kinds and ("link_evidence", "assetlinks") in kinds
    al = [x.value for x in f if x.kind == "link_evidence" and x.value["evidence_type"] == "assetlinks"][0]
    assert al["domain"] == "bank-a.example" and al["retrieved_at"] == "2026-11-01T10:00:00Z"
    assert len(al["cert_sha256"][0]) == 64 and al["source"].endswith("assetlinks.json")
    assert all(x.evidence_refs for x in f)
    Bundle(observations=[o], features=f)


def test_two_ads_same_app_different_domains_technical():
    mem = Mem()
    a = obs("a", mem, assetlinks="assetlinks_a.json", al_url="https://bank-a.example/.well-known/assetlinks.json")
    b = obs("b", mem, assetlinks="assetlinks_b.json", al_url="https://zupelnie-inna.example/.well-known/assetlinks.json")
    feats = run(a, b, mem=mem)
    pe = cmp(a, b, feats)
    assert pe.score == 1.0 and "powiązanie techniczne" in pe.explanation
    assert "bank-a.example" in pe.explanation and "zupelnie-inna.example" in pe.explanation
    assert pe.relation.value == "campaign_candidate" and all(pe.evidence_refs.values())


def test_page_link_is_weaker_than_technical_binding():
    mem = Mem()
    a = obs("a", mem, assetlinks="assetlinks_a.json", al_url="https://bank-a.example/.well-known/assetlinks.json")
    p = obs("p", mem, html="landing_pagelink.html")
    feats = run(a, p, mem=mem)
    assert ev(feats, p.observation_id)[0]["evidence_type"] == "page_link"
    pe = cmp(a, p, feats)
    assert pe.score == 0.6 and "page_link" in pe.explanation
    c = obs("c", mem, url=PLAY)
    assert cmp(a, c, run(a, c, mem=mem)).score == 1.0  # link sklepu jako cel reklamy + assetlinks
    assert cmp(p, c, run(p, c, mem=mem)).score == 0.6


def test_misleading_repack_different_fingerprint():
    mem = Mem()
    a = obs("a", mem, assetlinks="assetlinks_a.json", al_url="https://bank-a.example/.well-known/assetlinks.json")
    r = obs("r", mem, assetlinks="assetlinks_repack.json", al_url="https://fake.example/.well-known/assetlinks.json")
    pe = cmp(a, r, run(a, r, mem=mem))
    assert pe.score == 0.4 and "mylące" in pe.explanation


def test_different_app_and_missing_data():
    mem = Mem()
    a = obs("a", mem, url=PLAY)
    o = obs("o", mem, url="https://apps.apple.com/pl/app/x/id1234567890")
    feats = run(a, o, mem=mem)
    assert cmp(a, o, feats).score == 0.0  # ta sama marka, inny sklep/ID: nie łączymy
    n = obs("n", mem, url="https://example.com/landing")
    feats = run(a, n, mem=mem)
    assert cmp(a, n, feats).score is None


@pytest.mark.parametrize("sample,detail", [("assetlinks_empty.json", "brak deklaracji"), ("assetlinks_404.html", "brak deklaracji")])
def test_missing_assetlinks_declaration(sample, detail):
    mem = Mem()
    o = obs("m", mem, assetlinks=sample, al_url="https://x.example/.well-known/assetlinks.json")
    f, s = MobileAppExtractor().extract(o, reader=mem)
    assert not f and detail in s[0].detail
    assert s[0].state in (ExtractionState.ok, ExtractionState.failed)


def test_no_input():
    mem = Mem()
    oid = ids.observation_id("synthetic://z", "full_capture")
    sha = ids.sha256_bytes(b"z")
    a = Artifact(artifact_id=ids.artifact_id(oid, "other", sha, "z"), observation_id=oid, role=ArtifactRole.other,
                 local_ref="x/z", sha256=sha)
    o = Observation(observation_id=oid, canonical_id=ids.canonical_id([], url="synthetic://z"), source_ref="synthetic://z",
                    input_kind=InputKind.full_capture, artifacts=[a])
    f, s = MobileAppExtractor().extract(o, reader=mem)
    assert not f and s[0].state is ExtractionState.missing_input
