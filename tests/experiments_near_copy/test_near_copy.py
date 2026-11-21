import random

import pytest

from methadata.contracts import AdapterKind, Artifact, InputKind, Observation, ids
from methadata.contracts.registry import discover
from methadata.experiments.lexical import LexicalTextExtractor
from methadata.experiments.near_copy import (MinHasher, NearCopyComparator, containment, jaccard, lsh_candidates,
                                             rank_approx, rank_exact, shingles)
from methadata.experiments.near_copy.bench import measure, synthetic_corpus
from methadata.runner import run_experiment


def article(seed, n=300):
    r = random.Random(seed)
    return " ".join(f"s{seed}x{r.randrange(5000)}" for _ in range(n))


class Reader:
    def __init__(self): self.d = {}
    def open_bytes(self, ref): return self.d[ref.artifact_id]


def make(name, body, reader):
    oid = ids.observation_id(name, "full_capture")
    data = f"<html><head><title>T</title></head><body><article><p>{body}</p></article></body></html>".encode()
    sha = ids.sha256_bytes(data)
    a = Artifact(artifact_id=ids.artifact_id(oid, "landing_html", sha), observation_id=oid, role="landing_html",
                 local_ref=f"{name}.html", sha256=sha, media_type="text/html")
    reader.d[a.artifact_id] = data
    return Observation(observation_id=oid, canonical_id="can-" + name, source_ref=name,
                       input_kind=InputKind.full_capture, artifacts=[a])


def build(pages):
    r = Reader()
    obs = [make(n, t, r) for n, t in pages.items()]
    fm = {o.observation_id: list(LexicalTextExtractor().extract(o, reader=r)[0]) for o in obs}
    return obs, fm, r


def test_set_measures():
    a, b = shingles("aa bb cc dd ee ff", 3), shingles("aa bb cc dd xx yy", 3)
    assert jaccard(a, b) == pytest.approx(2 / 6) and containment(a, b) == pytest.approx(2 / 4)
    assert jaccard(set(), a) is None and containment(a, set()) is None
    assert shingles("ab cd", 5) == {("ab", "cd")}


def test_source_recovery_inserted_section():
    src = article(1)
    foreign = article(2, 200)
    copy = src[:len(src) // 2] + " " + foreign + " " + src[len(src) // 2:]
    obs, fm, _ = build({"src": src, "copy": copy, "other": article(3)})
    s, c, o = obs
    cont, jac = NearCopyComparator("containment"), NearCopyComparator("jaccard")
    ev = cont.compare(s, c, fm, run_id="r")[0]
    assert ev.score > 0.95  # źródło zawiera się w kopii
    assert jac.compare(s, c, fm, run_id="r")[0].score < 0.7  # dopisek obniża Jaccarda
    assert cont.compare(s, o, fm, run_id="r")[0].score < 0.05
    assert ev.evidence_refs["can-src"] and ev.evidence_refs["can-copy"] and "Wynik dokładny" in ev.explanation
    # odzyskanie źródła z korpusu: najwyższe zawieranie dla zapytania = kopii
    best = max((x for x in obs if x is not c), key=lambda x: cont.compare(c, x, fm, run_id="r")[0].score)
    assert best is s


def test_no_text_is_none():
    obs, fm, _ = build({"a": article(1), "b": ""})
    assert NearCopyComparator().compare(obs[0], obs[1], fm, run_id="r")[0].score is None


def test_approx_ranking_matches_exact():
    base = article(7, 400).split()
    r = random.Random(0)
    corpus = {}
    for i, rate in enumerate([0.02, 0.05, 0.1, 0.2, 0.4]):
        corpus[f"v{i}"] = " ".join(w if r.random() > rate else "zz%d" % r.randrange(9999) for w in base)
    for i in range(10):
        corpus[f"n{i}"] = article(100 + i, 400)
    sh = {k: shingles(t) for k, t in corpus.items()}
    q = shingles(" ".join(base))
    ex = [k for k, _ in rank_exact(q, sh)]
    mh = MinHasher(256, seed=3)
    ap = [k for k, _ in rank_approx(mh.signature(q), {k: mh.signature(s) for k, s in sh.items()})]
    assert ex[:5] == ap[:5] == ["v0", "v1", "v2", "v3", "v4"]


def test_minhash_deterministic_and_estimates_jaccard():
    a, b = shingles(article(1)), shingles(article(1)[:1500])
    h = MinHasher(256, seed=1)
    assert h.signature(a) == MinHasher(256, seed=1).signature(a)
    assert abs(MinHasher.estimate(h.signature(a), h.signature(b)) - jaccard(a, b)) < 0.1
    assert h.signature([]) is None


def test_lsh_recall_and_speedup_fewer_pairs():
    docs, _ = synthetic_corpus(n_families=15, variants=6, seed=2)
    m = measure(docs, bands=64)
    assert m["pairs_true"] > 0 and m["recall"] >= 0.95 and m["candidates"] < m["pairs_all"] / 3
    sig = {"a": (1, 2, 3, 4), "b": (1, 2, 9, 9), "c": (7, 7, 7, 7)}
    assert lsh_candidates(sig, bands=2) == {("a", "b")}


def test_discover_and_runner():
    found = discover()
    assert found["near-copy-jaccard-v1"].spec.kind is AdapterKind.comparator
    assert found["near-copy-containment-v1"].spec.kind is AdapterKind.comparator
    src = article(1)
    obs, _, r = build({"src": src, "copy": src + " " + article(2, 50), "o": article(3)})
    adapters = {k: v for k, v in found.items()}

    cl = next(v for v in found.values() if v.spec.kind is AdapterKind.clusterer)
    cfg = {"dataset_version": "t", "seed": 0, "relation": "shared_narrative", "clusterer": cl.spec.id,
           "extractors": ["lexical-text-extractor"], "comparators": ["near-copy-containment-v1"],
           "cluster_config": {"threshold": 0.9, "similarity_version": "near-copy-1", "dataset_version": "t"}}
    res = run_experiment(cfg, obs, adapters, reader=r)
    sc = {tuple(p.pair): p.score for p in res["bundle"].pairs}
    assert sc[("can-copy", "can-src")] > 0.95 and sc[("can-o", "can-src")] < 0.05
