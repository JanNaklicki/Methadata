import pytest

from methadata.contracts import Artifact, Feature, InputKind, Observation, ids
from methadata.experiments.assets import (AssetHashExtractor, AssetJaccardComparator, WeightedAssetJaccardComparator,
                                          corpus_frequencies, jaccard, weighted_jaccard)


def H(i):
    return ids.sha256_bytes(f"img{i}".encode())


def obs(name, hashes, shot=None):
    oid = ids.observation_id(name, "full_capture")
    arts = []
    for i, h in enumerate(hashes):
        arts.append(Artifact(artifact_id=ids.artifact_id(oid, "creative_image", h, f"{i}"), observation_id=oid,
                             role="creative_image", local_ref=f"{name}/{i}.png", sha256=h, media_type="image/png"))
    if shot:
        arts.append(Artifact(artifact_id=ids.artifact_id(oid, "landing_screenshot", shot), observation_id=oid,
                             role="landing_screenshot", local_ref=f"{name}/s.png", sha256=shot))
    if not arts:
        arts.append(Artifact(artifact_id=ids.artifact_id(oid, "other", H(99)), observation_id=oid, role="other",
                             local_ref=f"{name}/x", sha256=H(99)))
    return Observation(observation_id=oid, canonical_id="can-" + name, source_ref=name, input_kind=InputKind.full_capture, artifacts=arts)


def feats(o):
    f, st = AssetHashExtractor().extract(o, reader=None)
    assert st[0].state.value == "ok"
    return f


def run(cmp, a, b, fm):
    return cmp.compare(a, b, fm, run_id="run-1")[0]


def test_set_math():
    assert jaccard({"a", "b"}, {"a", "b"}) == 1
    assert jaccard({"a"}, {"b"}) == 0
    assert jaccard(set(), set()) is None
    assert jaccard(set(range(5)), set(range(1, 6))) == 4 / 6


def test_weighted_math():
    f = {"a": 1, "b": 4}
    # przecięcie {b}: 1/4; suma {a,b}: 1 + 1/4
    assert weighted_jaccard({"a", "b"}, {"b"}, f) == pytest.approx(0.25 / 1.25)
    assert weighted_jaccard(set(), set(), f) is None


def test_four_of_eighteen():
    common = [H(i) for i in range(4)]
    a = obs("a", common + [H(100 + i) for i in range(7)])
    b = obs("b", common + [H(200 + i) for i in range(7)])
    assert len(set(feats(a)[i].value for i in range(11)) | set(feats(b)[i].value for i in range(11))) == 18
    fm = {a.observation_id: feats(a), b.observation_id: feats(b)}
    ev = run(AssetJaccardComparator(), a, b, fm)
    assert ev.score == pytest.approx(4 / 18)
    assert ev.pair == tuple(sorted((a.canonical_id, b.canonical_id)))
    assert len(ev.evidence_refs[a.canonical_id]) == 4 and "4/18" in ev.explanation
    for h in common:
        assert h[:12] in ev.explanation


def test_identical_and_disjoint():
    a, b = obs("a", [H(1), H(2)]), obs("b", [H(2), H(1)])
    c = obs("c", [H(3)])
    fm = {o.observation_id: feats(o) for o in (a, b, c)}
    assert run(AssetJaccardComparator(), a, b, fm).score == 1
    assert run(AssetJaccardComparator(), a, c, fm).score == 0


def test_empty_is_no_basis_and_screenshot_separate():
    a, b = obs("a", [], shot=H(7)), obs("b", [], shot=H(7))
    fm = {o.observation_id: feats(o) for o in (a, b)}
    assert {f.kind for f in fm[a.observation_id]} == {"screenshot_sha256"}
    ev = run(AssetJaccardComparator(), a, b, fm)
    assert ev.score is None and "brak podstaw" in ev.explanation
    c = obs("c", [H(1)])
    fm[c.observation_id] = feats(c)
    assert run(AssetJaccardComparator(), a, c, fm).score is None


def test_weighted_downweights_common_asset():
    # H(0) (logo) jest w 4 materiałach, H(1) tylko w a i b
    a, b = obs("a", [H(0), H(1)]), obs("b", [H(0), H(2)])
    others = [obs(n, [H(0)]) for n in "xyz"]
    fm = {o.observation_id: feats(o) for o in [a, b, *others]}
    freq = corpus_frequencies(fm)
    assert freq[H(0)] == 5 and freq[H(1)] == 1
    plain = run(AssetJaccardComparator(), a, b, fm).score
    w = run(WeightedAssetJaccardComparator(freq), a, b, fm)
    assert plain == pytest.approx(1 / 3)
    assert w.score == pytest.approx(0.2 / 2.2) and w.score < plain
    assert "korpus: 5" in w.explanation


def test_order_independent_and_adapters_valid():
    a, b = obs("a", [H(1)]), obs("b", [H(1), H(2)])
    fm = {o.observation_id: feats(o) for o in (a, b)}
    c = AssetJaccardComparator()
    assert run(c, a, b, fm).pair_evidence_id == run(c, b, a, fm).pair_evidence_id
    from methadata.contracts.interfaces import check_adapter
    from methadata.experiments.assets import get_adapters
    for ad in get_adapters():
        check_adapter(ad)


def test_discover_finds_adapters():
    from methadata.contracts import discover
    found = discover()
    for i in ("assets-hash-extractor", "assets-jaccard-v1", "assets-weighted-jaccard-v1"):
        assert i in found
