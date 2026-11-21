import pytest

from methadata.contracts.interfaces import AdapterKind
from methadata.contracts.models import ArtifactRole, ExtractionState
from methadata.contracts.registry import discover
from methadata.sources.media_provenance import (MediaProvenanceComparator, MediaProvenanceExtractor, read_provenance,
                                                validator_info)
from methadata.sources.media_provenance.reader import Unsupported


def extract(obs, mem):
    return MediaProvenanceExtractor().extract(obs, reader=mem)


def by(feats, kind):
    return [f.value for f in feats if f.kind == kind]


@pytest.fixture
def pair(samples, make_obs):
    """Oryginał (zdjęcie) i dwa montaże ze wspólnym składnikiem źródłowym."""
    src = samples.sign(samples.image((200, 30, 30)), "source.jpg")
    m1 = samples.sign(samples.image((10, 10, 200)), "montage1.jpg", "compositeSynthetic", [("source.jpg", src)])
    m2 = samples.sign(samples.image((10, 200, 10)), "montage2.jpg", "compositeSynthetic", [("source.jpg", src)])
    other = samples.sign(samples.image((90, 90, 90)), "other.jpg")
    return src, m1, m2, other


def test_registered():
    d = discover()
    assert d["source-media-provenance-extractor"].spec.kind is AdapterKind.extractor
    assert d["source-media-provenance-compare"].spec.kind is AdapterKind.comparator


def test_validator_version_documented():
    v = validator_info()
    assert v["engine"] == "c2pa-python" and v["library"] and v["sdk"]


def test_extract_manifest_status_and_ingredients(pair, make_obs, mem):
    src, m1, _, _ = pair
    feats, sts = extract(make_obs("m1.jpg", m1), mem)
    assert [s.state for s in sts] == [ExtractionState.ok]
    assert by(feats, "c2pa_present") == [True]
    assert by(feats, "c2pa_validation_state") == ["Valid"]  # certyfikat testowy: Valid, nie Trusted
    val = by(feats, "c2pa_validation")[0]
    assert val["engine"] == "c2pa-python" and val["sdk_version"] and val["spec_version"]
    assert "signingCredential.untrusted" in val["failure_codes"]
    src_label = read_provenance(src).active_manifest
    assert by(feats, "c2pa_ancestor_manifests") == [[src_label]]
    ing = by(feats, "c2pa_ingredients")[0][0]
    assert ing["manifest_label"] == src_label and ing["relationship"] == "componentOf" and ing["title"] == "source.jpg"
    assert "c2pa.created|compositeSynthetic" in by(feats, "c2pa_actions")[0]
    assert by(feats, "c2pa_signer")[0]["common_name"] == "Test Signer"
    assert by(feats, "record_provenance")[0]["retrieved_at"] == "2026-11-01T10:00:00Z"
    assert all(f.evidence_refs for f in feats)


def test_two_montages_share_source(pair, make_obs, mem):
    _, m1, m2, _ = pair
    o1, o2 = make_obs("m1.jpg", m1), make_obs("m2.jpg", m2)
    feats = {o.observation_id: extract(o, mem)[0] for o in (o1, o2)}
    ev, = MediaProvenanceComparator().compare(o1, o2, feats, run_id="run-1")
    assert ev.score == 0.9 and "wspólny materiał źródłowy" in ev.explanation and "Valid, nie Trusted" in ev.explanation
    assert ev.evidence_refs[o1.canonical_id] and ev.evidence_refs[o2.canonical_id]


def test_original_and_montage_derivation(pair, make_obs, mem):
    src, m1, _, _ = pair
    o_src, o_m = make_obs("src.jpg", src), make_obs("m1.jpg", m1)
    feats = {o.observation_id: extract(o, mem)[0] for o in (o_src, o_m)}
    ev, = MediaProvenanceComparator().compare(o_m, o_src, feats, run_id="run-1")
    assert ev.score == 1.0 and "pochodzi od drugiego" in ev.explanation


def test_copy_without_manifest_and_screenshot_is_no_data(pair, make_obs, mem, samples):
    src, m1, _, _ = pair
    stripped = samples.image((200, 30, 30))  # ta sama treść wizualna, bez manifestu
    shot = samples.image((1, 2, 3), "PNG")
    for data, role, mt in ((stripped, ArtifactRole.creative_image, "image/jpeg"),
                           (shot, ArtifactRole.landing_screenshot, "image/png")):
        o = make_obs("c.bin", data, role, mt)
        feats, sts = extract(o, mem)
        assert by(feats, "c2pa_present") == [False] and sts[0].state is ExtractionState.ok
        assert not by(feats, "c2pa_validation_state")
        o_m = make_obs("m1.jpg", m1)
        fm = {o.observation_id: feats, o_m.observation_id: extract(o_m, mem)[0]}
        ev, = MediaProvenanceComparator().compare(o, o_m, fm, run_id="run-1")
        assert ev.score is None and "brak danych" in ev.explanation


def test_misleading_cases(pair, make_obs, mem, samples):
    src, m1, m2, other = pair
    # (1) ten sam generator/podpisujący, brak wspólnego składnika: nie dowód
    oa, ob = make_obs("a.jpg", other), make_obs("m2.jpg", m2)
    f = {o.observation_id: extract(o, mem)[0] for o in (oa, ob)}
    ev, = MediaProvenanceComparator().compare(oa, ob, f, run_id="r")
    assert ev.score is None and "bez wspólnego składnika" in ev.explanation
    # (2) wspólne źródło, ale jeden manifest naruszony po podpisie -> Invalid, wynik obniżony
    t = bytearray(m1)
    t[t.find(b"\xff\xda") + 40] ^= 0xFF
    o1, o2 = make_obs("tampered.jpg", bytes(t)), make_obs("m2.jpg", m2)
    f = {o.observation_id: extract(o, mem)[0] for o in (o1, o2)}
    assert by(f[o1.observation_id], "c2pa_validation_state") == ["Invalid"]
    ev, = MediaProvenanceComparator().compare(o1, o2, f, run_id="r")
    assert ev.score == 0.3 and "MYLĄCE" in ev.explanation


def test_unsupported_and_unreadable(make_obs, mem):
    o = make_obs("x.bin", b"hello world, not an image", ArtifactRole.creative_image, "image/jpeg")
    _, sts = extract(o, mem)
    assert sts[0].state is ExtractionState.unsupported
    o = make_obs("broken.jpg", b"\xff\xd8\xff\xe0" + b"x" * 40)
    _, sts = extract(o, mem)
    assert sts[0].state is ExtractionState.failed


def test_fallback_jumbf_parser_jpeg_and_png(pair, samples):
    src, m1, _, _ = pair
    full = read_provenance(m1)
    fb = read_provenance(m1, force_fallback=True)
    assert fb.engine == "jumbf-fallback" and fb.present and fb.validation_state == "Unverified"
    assert fb.active_manifest == full.active_manifest and fb.ancestor_manifests == full.ancestor_manifests
    png = samples.sign(samples.image((5, 5, 5), "PNG"), "p.png", mime="image/png")
    fp = read_provenance(png, force_fallback=True)
    assert fp.present and fp.active_manifest == read_provenance(png).active_manifest
    assert read_provenance(samples.image((1, 1, 1)), force_fallback=True).present is False
    with pytest.raises(Unsupported):
        read_provenance(b"RIFF\x00\x00\x00\x00WEBPxxxx", force_fallback=True)


def test_fallback_comparison_is_capped(pair, make_obs, mem, monkeypatch):
    _, m1, m2, _ = pair
    import methadata.sources.media_provenance.reader as r
    monkeypatch.setattr(r, "c2pa_available", lambda: False)
    o1, o2 = make_obs("m1.jpg", m1), make_obs("m2.jpg", m2)
    f = {o.observation_id: extract(o, mem)[0] for o in (o1, o2)}
    ev, = MediaProvenanceComparator().compare(o1, o2, f, run_id="r")
    assert ev.score == 0.6 and "nie został zwalidowany" in ev.explanation
