from pathlib import Path

import pytest

from methadata.contracts import ids
from methadata.contracts.interfaces import AdapterKind
from methadata.contracts.models import Artifact, ArtifactRole, Bundle, ExtractionState, InputKind, Observation
from methadata.contracts.registry import discover
from methadata.sources.affiliate import AffiliateComparator, AffiliateExtractor, parse_affiliate_url, parse_text_codes

S = Path(__file__).parent / "samples"


class Mem:
    def __init__(self):
        self.d = {}

    def open_bytes(self, ref):
        return self.d[ref.artifact_id]


def obs(name, mem, *, url=None, html=None, doc=None):
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
    if doc:
        add(ArtifactRole.document, (S / doc).read_bytes(), local_ref="x/d.txt", media_type="text/plain")
    return Observation(observation_id=oid, canonical_id=ids.canonical_id([], url="synthetic://" + name),
                       source_ref="synthetic://" + name, tags=["synthetic"], capture_time="2026-11-01T10:00:00Z",
                       input_kind=InputKind.url_only if url and not html and not doc else InputKind.full_capture,
                       artifacts=arts)


def run(*observations, mem):
    ex, feats = AffiliateExtractor(), {}
    for o in observations:
        f, s = ex.extract(o, reader=mem)
        feats[o.observation_id] = f
        assert all(x.state is ExtractionState.ok for x in s), s
    return feats


def cmp(a, b, feats):
    [pe] = AffiliateComparator().compare(a, b, feats, run_id="r1")
    return pe


def test_registered():
    assert {"source-affiliate-extractor", "source-affiliate-compare"} <= set(discover())
    assert discover(AdapterKind.comparator)["source-affiliate-compare"].spec.kind is AdapterKind.comparator


def test_parse_urls_compound_key():
    [r] = parse_affiliate_url("https://www.awin1.com/cread.php?awinmid=1234&awinaffid=777001")
    assert (r["program"], r["partner_id"], r["merchant"], r["role"]) == ("awin", "777001", "1234", "partner_id")
    [r] = parse_affiliate_url("https://www.amazon.pl/dp/B000?tag=jan-21")
    assert (r["program"], r["partner_id"]) == ("amazon_associates", "jan-21")
    [r] = parse_affiliate_url("https://www.anrdoezrs.net/click-100200-300400")
    assert (r["program"], r["partner_id"]) == ("cj", "100200")
    [r] = parse_affiliate_url("https://jan.sklep.hop.clickbank.net/")
    assert (r["program"], r["partner_id"], r["merchant"]) == ("clickbank", "jan", "sklep")
    [r] = parse_affiliate_url("https://sklep.example/?ref=ZNAJOMY1")
    assert r["role"] == "referral_code" and r["program"] == "shop:sklep.example"
    assert parse_affiliate_url("https://example.com/oferta") == [] and parse_affiliate_url("not a url") == []


def test_promo_vs_partner_distinguished():
    recs = parse_affiliate_url("https://www.awin1.com/cread.php?awinaffid=5&awinmid=1&promo=RABAT20")
    assert {r["role"] for r in recs} == {"partner_id", "promo_code"}
    t = parse_text_codes("Użyj kodu RABAT20! Kod polecający: ABC123XY. promo code the")
    assert [(x["role"], x["code"]) for x in t] == [("promo_code", "RABAT20"), ("referral_code", "ABC123XY")]


def test_features_valid_and_traceable():
    mem = Mem()
    o = obs("a", mem, html="landing_awin.html")
    f = run(o, mem=mem)[o.observation_id]
    ev = [x.value for x in f if x.kind == "affiliate_evidence"]
    assert {e["key"] for e in ev if e["role"] == "partner_id"} == {"awin:777001"}
    assert any(e["role"] == "promo_code" and e["code"] == "RABAT20" and e["evidence_type"] == "text" for e in ev)
    assert not any(e.get("code") == "FAKE99" for e in ev)  # skrypty pomijane
    assert all(e["retrieved_at"] == "2026-11-01T10:00:00Z" and e["status"] == "ok" for e in ev)
    assert all(x.evidence_refs for x in f)
    Bundle(observations=[o], features=f)


def test_same_partner_across_awin_domains():
    mem = Mem()
    a, b = obs("a", mem, html="landing_awin.html"), obs("b", mem, html="landing_zenaps.html")
    feats = run(a, b, mem=mem)
    pe = cmp(a, b, feats)
    assert pe.score == 1.0 and "777001" in pe.explanation and "awin1.com" in pe.explanation and "zenaps.com" in pe.explanation
    nd = {(x.value["network"], x.value["domain"]) for f in feats.values() for x in f if x.kind == "network_domain"}
    assert nd == {("awin", "awin1.com"), ("awin", "zenaps.com")}
    assert all(pe.evidence_refs.values())


def test_popular_promo_code_across_programs_is_misleading():
    mem = Mem()
    a = obs("a", mem, url="https://www.awin1.com/cread.php?awinaffid=5&awinmid=1&promo=RABAT20")
    b = obs("b", mem, url="https://sklep.example/?coupon=RABAT20")
    pe = cmp(a, b, run(a, b, mem=mem))
    assert pe.score == 0.2 and "ogólnopromocyjny" in pe.explanation


def test_same_id_different_programs_misleading():
    mem = Mem()
    a = obs("a", mem, url="https://www.awin1.com/cread.php?awinaffid=777001")
    b = obs("b", mem, url="https://www.rakuten.example/x")
    c = obs("c", mem, url="https://click.linksynergy.com/deeplink?id=777001&mid=2")
    pe = cmp(a, c, run(a, c, mem=mem))
    assert pe.score == 0.2 and "różnych programach" in pe.explanation
    assert cmp(a, b, run(a, b, mem=mem)).score is None


def test_different_partner_same_network_and_referral_code():
    mem = Mem()
    a = obs("a", mem, url="https://www.awin1.com/cread.php?awinaffid=1")
    b = obs("b", mem, url="https://www.awin1.com/cread.php?awinaffid=2")
    assert cmp(a, b, run(a, b, mem=mem)).score == 0.3
    d = obs("d", mem, doc="creator_description.txt")
    e = obs("e", mem, html="landing_zenaps.html")
    f = obs("f", mem, url="https://sklep.example/?ref=ABC123XY")
    assert cmp(d, f, run(d, f, mem=mem)).score == 0.4  # program z opisu nieustalony
    assert cmp(d, e, run(d, e, mem=mem)).score == 0.0


def test_missing_data():
    mem = Mem()
    n = obs("n", mem, html="landing_none.html")
    f, s = AffiliateExtractor().extract(n, reader=mem)
    assert not f and "brak" in s[0].detail
    a = obs("a", mem, url="https://www.awin1.com/cread.php?awinaffid=1")
    assert cmp(a, n, run(a, n, mem=mem)).score is None


def test_no_input():
    mem = Mem()
    oid = ids.observation_id("synthetic://z", "full_capture")
    sha = ids.sha256_bytes(b"z")
    a = Artifact(artifact_id=ids.artifact_id(oid, "other", sha, "z"), observation_id=oid, role=ArtifactRole.other,
                 local_ref="x/z", sha256=sha)
    o = Observation(observation_id=oid, canonical_id=ids.canonical_id([], url="synthetic://z"), source_ref="synthetic://z",
                    input_kind=InputKind.full_capture, artifacts=[a])
    f, s = AffiliateExtractor().extract(o, reader=mem)
    assert not f and s[0].state is ExtractionState.missing_input


def test_real_data_urls_scan():
    """Próbka rzeczywista: URL-e z kolekcji (tylko tekst, bez wykonywania). Skip, gdy brak danych."""
    from methadata.devtools.realdata import SCAM_LINKS

    if not SCAM_LINKS.exists():
        pytest.skip("brak scam_links.dec")
    urls = [l.strip() for l in SCAM_LINKS.read_text(errors="replace").splitlines()[:2000] if l.strip().startswith("http")]
    hits = [r for u in urls for r in parse_affiliate_url(u)]  # nie może rzucać wyjątków
    assert isinstance(hits, list)
