import json

import pytest

from methadata.contracts import ids
from methadata.contracts.interfaces import AdapterKind
from methadata.contracts.models import Artifact, ArtifactRole, ExtractionState, InputKind, Observation
from methadata.contracts.registry import discover
from methadata.devtools.realdata import BACKUP
from methadata.sources.offer_id import OfferIdComparator, OfferIdExtractor, check_digit_ok, normalize_gtin


class Mem:
    def __init__(self):
        self.d = {}

    def open_bytes(self, ref):
        return self.d[ref.artifact_id]


def obs(name, body, mem, url=None):
    data = body.encode()
    src = url or "synthetic://" + name
    oid = ids.observation_id(src, "full_capture")
    sha = ids.sha256_bytes(data)
    a = Artifact(artifact_id=ids.artifact_id(oid, "landing_html", sha, name + ".html"), observation_id=oid,
                 role=ArtifactRole.landing_html, local_ref="x/" + name + ".html", sha256=sha, media_type="text/html",
                 origin=name + ".html")
    mem.d[a.artifact_id] = data
    return Observation(observation_id=oid, canonical_id=ids.canonical_id([("landing_html", sha)]), source_ref=src,
                       input_kind=InputKind.creative_only, artifacts=[a], tags=["synthetic"], capture_time="2026-11-01T10:00:00Z")


def ld(sku=None, gtin=None, seller=None, url=None):
    d = {"@context": "https://schema.org", "@type": "Product", "name": "Słuchawki",
         "offers": {"@type": "Offer", "seller": {"@type": "Organization", "name": seller} if seller else None}}
    if sku:
        d["sku"] = sku
    if gtin:
        d["gtin13"] = gtin
    if url:
        d["offers"]["url"] = url
    return f'<html><head><script type="application/ld+json">{json.dumps(d)}</script></head><body></body></html>'


def run(*pages):
    pages = [(p[0] + f'<!--{n}-->', p[1]) if isinstance(p, tuple) else p + f'<!--{n}-->' for n, p in enumerate(pages)]
    mem, ex, os_, feats = Mem(), OfferIdExtractor(), [], {}
    for n, p in enumerate(pages):
        body, url = p if isinstance(p, tuple) else (p, None)
        o = obs(f"s{n}", body, mem, url)
        f, s = ex.extract(o, reader=mem)
        assert all(x.state is ExtractionState.ok for x in s)
        os_.append(o)
        feats[o.observation_id] = f
    return os_, feats


def cmp(os_, feats):
    (e,) = OfferIdComparator().compare(os_[0], os_[1], feats, run_id="r1")
    return e


def kinds(f, k):
    return [x.value for x in f if x.kind == k]


def test_registered():
    assert {"source-offer-id-extractor", "source-offer-id-compare"} <= set(discover())
    assert discover(AdapterKind.comparator)["source-offer-id-compare"].spec.kind is AdapterKind.comparator


def test_gtin_checksum():
    assert check_digit_ok("5901234123457") and check_digit_ok("4006381333931")
    assert not check_digit_ok("5901234123458")
    assert normalize_gtin("5901234123457") == ("05901234123457", True)
    assert normalize_gtin("590-1234-123457")[1] and normalize_gtin("12")[0] is None


def test_extract_distinct_kinds_and_provenance():
    os_, feats = run((ld(sku="AB-1", gtin="5901234123457", seller="Sklep Alfa",
                         url="https://allegro.pl/oferta/sluchawki-12345678901"), "https://allegro.pl/oferta/sluchawki-12345678901"))
    f = feats[os_[0].observation_id]
    assert kinds(f, "gtin")[0]["gtin14"] == "05901234123457" and kinds(f, "gtin")[0]["valid"]
    sku = kinds(f, "sku")[0]
    assert sku["sku"] == "AB-1" and sku["issuer"] == "Sklep Alfa"
    off = kinds(f, "offer_id")[0]
    assert off["id"] == "12345678901" and off["marketplace"] == "allegro"
    assert kinds(f, "seller_id")[0]["id"] == "Sklep Alfa"
    for x in f:
        assert x.value["provenance"]["artifact_id"] and x.value["captured_at"] and x.value["status"]


def test_invalid_gtin_flagged_not_matched():
    os_, feats = run(ld(gtin="5901234123458"), ld(gtin="5901234123458"))
    assert kinds(feats[os_[0].observation_id], "gtin")[0]["status"] == "invalid_checksum"
    assert cmp(os_, feats).score == 0.0


def test_success_same_gtin_translated_names():
    a = ld(gtin="5901234123457", seller="Sklep Alfa").replace("Słuchawki", "Headphones")
    b = ld(gtin="5901234123457", seller="Shop Beta").replace("Słuchawki", "Kopfhörer")
    os_, feats = run(a, b)
    e = cmp(os_, feats)
    assert e.score == 0.9 and "GTIN" in e.explanation and e.evidence_refs


def test_same_offer_several_ads():
    u1 = "https://allegro.pl/oferta/sluchawki-12345678901?utm=a"
    u2 = "https://allegro.pl/oferta/inny-tytul-12345678901"
    os_, feats = run((ld(), u1), (ld(), u2))
    e = cmp(os_, feats)
    assert e.score == 0.97 and "oferty" in e.explanation


def test_misleading_same_sku_other_sellers():
    os_, feats = run(ld(sku="X-100", seller="Sklep Alfa"), ld(sku="X-100", seller="Sklep Beta"))
    e = cmp(os_, feats)
    assert e.score == 0.15 and "mylące" in e.explanation


def test_same_sku_same_seller_and_unknown_seller():
    os_, feats = run(ld(sku="X-100", seller="Sklep Alfa"), ld(sku="X-100", seller="sklep alfa"))
    assert cmp(os_, feats).score == 0.7
    os_, feats = run(ld(sku="X-100"), ld(sku="X-100", seller="Sklep Beta"))
    assert cmp(os_, feats).score == 0.12


def test_control_identical_product_independent_sellers_beats_sku_collision():
    os_, feats = run(ld(sku="A1", gtin="5901234123457", seller="Alfa"), ld(sku="B9", gtin="5901234123457", seller="Beta"))
    assert cmp(os_, feats).score == 0.9


def test_different_gtin_and_missing_data():
    os_, feats = run(ld(gtin="5901234123457"), ld(gtin="4006381333931"))
    assert cmp(os_, feats).score == 0.0
    os_, feats = run(ld(gtin="5901234123457"), "<html><body>Brak danych</body></html>")
    assert cmp(os_, feats).score is None


def test_label_text_and_ocr():
    mem, ex = Mem(), OfferIdExtractor()
    o = obs("t", "<html><body>Sprzedawca: Sklep Gamma EAN: 5901234123457 SKU: ZZ-77 Numer oferty: 998877</body></html>", mem,
            "https://olx.pl/d/oferta/x-IDabc12.html")
    f, _ = ex.extract(o, reader=mem, optional={"ocr_texts": {o.artifacts[0].artifact_id: "EAN 4006381333931"}})
    assert {v["gtin14"] for v in kinds(f, "gtin")} == {"04006381333931"}
    f2, _ = ex.extract(o, reader=mem)
    assert kinds(f2, "gtin")[0]["gtin14"] == "05901234123457" and kinds(f2, "sku")[0]["issuer"] == "Sklep Gamma"
    assert any(v["id"] == "abc12" and v["marketplace"] == "olx" for v in kinds(f2, "offer_id"))


def test_no_artifacts_missing_input():
    mem = Mem()
    o = obs("t", "x", mem).model_copy(update={"artifacts": []})
    f, s = OfferIdExtractor().extract(o, reader=mem)
    assert f == [] and s[0].state is ExtractionState.missing_input


@pytest.mark.skipif(not BACKUP.is_dir(), reason="brak danych rzeczywistych")
def test_real_backup_pages_do_not_crash():
    ex, mem, n = OfferIdExtractor(), Mem(), 0
    for p in sorted(BACKUP.glob("*/*.html"))[:20]:
        o = obs(f"r{n}", p.read_text(encoding="utf-8", errors="replace"), mem)
        _, s = ex.extract(o, reader=mem)
        assert s and s[0].state is ExtractionState.ok
        n += 1
