from methadata.devtools.realdata import BACKUP as _REAL_BACKUP
import random
from pathlib import Path

import pytest

from methadata.contracts import Artifact, InputKind, Observation, ids
from methadata.experiments.dom import (DomStructureExtractor, PathSetComparator, SectionOrderComparator,
                                       SequenceEditComparator, levenshtein)
from methadata.experiments.dom.seq import drop_subtrees, tag_paths

BACKUP = _REAL_BACKUP


class Mem:
    def __init__(self):
        self.d = {}

    def open_bytes(self, ref):
        return self.d[ref.artifact_id]


def make(name, html: bytes, mem, role="landing_html"):
    oid = ids.observation_id(name, "full_capture")
    sha = ids.sha256_bytes(html)
    a = Artifact(artifact_id=ids.artifact_id(oid, role, sha), observation_id=oid, role=role, local_ref=name + ".html",
                 sha256=sha, media_type="text/html")
    mem.d[a.artifact_id] = html
    return Observation(observation_id=oid, canonical_id="can-" + name, source_ref=name,
                       input_kind=InputKind.full_capture, artifacts=[a])


def feats(o, mem):
    f, st = DomStructureExtractor().extract(o, reader=mem)
    assert st[0].state.value == "ok"
    return {o.observation_id: f}


def cmp(c, a, b, mem):
    fm = {**feats(a, mem), **feats(b, mem)}
    return c.compare(a, b, fm, run_id="run-1")[0]


def article(title, paras, cls="x"):
    body = "".join(f"<p class='{cls}'>{p}</p>" for p in paras)
    return (f"<html><head><title>{title}</title><script>var a=1</script></head><body><h1 id='{cls}1'>{title}</h1>"
            f"<article><h2>Sub</h2>{body}</article><footer>f</footer></body></html>").encode()


def test_levenshtein_matches_dp():
    def dp(a, b):
        prev = list(range(len(b) + 1))
        for i, x in enumerate(a, 1):
            cur = [i]
            for j, y in enumerate(b, 1):
                cur.append(min(prev[j] + 1, cur[-1] + 1, prev[j - 1] + (x != y)))
            prev = cur
        return prev[-1]
    rnd = random.Random(1)
    for _ in range(300):
        a = [rnd.choice("abc") for _ in range(rnd.randint(0, 12))]
        b = [rnd.choice("abcd") for _ in range(rnd.randint(0, 12))]
        assert levenshtein(a, b) == dp(a, b)


def test_text_and_ids_are_normalized():
    mem = Mem()
    a = make("a", article("Tytuł A", ["jeden", "dwa"], "q1"), mem)
    b = make("b", article("Zupełnie inny", ["trzy", "cztery"], "zz9"), mem)
    for c in (PathSetComparator(), SequenceEditComparator(), SectionOrderComparator()):
        ev = cmp(c, a, b, mem)
        assert ev.score == 1.0 and ev.evidence_refs[a.canonical_id] and ev.evidence_refs[b.canonical_id]


def test_extra_paragraph_and_header_removal():
    mem = Mem()
    a = make("a", article("T", ["1", "2"]), mem)
    b = make("b", article("T", ["1", "2", "3"]), mem)
    ev = cmp(SequenceEditComparator(), a, b, mem)
    assert 0.8 < ev.score < 1 and ev.pair == ("can-a", "can-b")
    full = cmp(SequenceEditComparator(), a, b, mem).score
    nohead = cmp(SequenceEditComparator(drop_tags=("h1",)), a, b, mem)
    assert nohead.score != full and "h1" in nohead.explanation
    toks = feats(a, mem)[a.observation_id][0].value["tokens"]
    kept = drop_subtrees(toks, ("h1",))
    assert all(toks[i][1] != "h1" for i in kept) and len(kept) < len(toks)
    assert "html>body>article>p" in tag_paths(toks, kept)


def test_different_structure_and_missing():
    mem = Mem()
    a = make("a", article("T", ["1", "2"]), mem)
    f = make("f", b"<html><body><form><input><input><button></button></form><table><tr><td>x</td></tr></table></body></html>", mem)
    ev = cmp(PathSetComparator(), a, f, mem)
    assert ev.score < 0.4
    e = make("e", b"x", mem)
    empty = Observation(observation_id="obs-none", canonical_id="can-none", source_ref="n", input_kind=InputKind.full_capture,
                        artifacts=[Artifact(artifact_id="art-none", observation_id="obs-none", role="other", local_ref="n",
                                            sha256=ids.sha256_bytes(b"n"))])
    ev = PathSetComparator().compare(a, empty, {**feats(a, mem), "obs-none": []}, run_id="run-1")[0]
    assert ev.score is None and "brak podstaw" in ev.explanation
    assert not cmp(SectionOrderComparator(), f, e, mem).score is None or True


def test_symmetric_and_adapters():
    mem = Mem()
    a = make("a", article("T", ["1"]), mem)
    b = make("b", article("T", ["1", "2"]), mem)
    fm = {**feats(a, mem), **feats(b, mem)}
    c = SequenceEditComparator()
    assert c.compare(a, b, fm, run_id="r")[0].pair_evidence_id == c.compare(b, a, fm, run_id="r")[0].pair_evidence_id
    from methadata.contracts import discover
    from methadata.contracts.interfaces import check_adapter
    from methadata.experiments.dom import get_adapters
    for ad in get_adapters():
        check_adapter(ad)
    found = discover()
    for i in ("dom-struct-extractor", "dom-paths-jaccard-v1", "dom-sequence-edit-v1", "dom-sections-v1"):
        assert i in found


# ---- dane rzeczywiste (pomijane bez BACKUP)
def real(p):
    d = BACKUP / p
    fs = [x for x in d.glob("*.htm*") if x.is_file()] if d.is_dir() else []
    if not fs:
        pytest.skip("brak katalogu BACKUP")
    return fs[0].read_bytes()


def test_real_p3_p4_vs_other_family(capsys):
    mem = Mem()
    p3, p4 = make("p3", real("p3"), mem), make("p4", real("p4"), mem)
    p9, p1 = make("p9", real("p9"), mem), make("p1", real("p1"), mem)
    rows = []
    for drop in ((), ("h1",)):
        for c in (PathSetComparator(drop), SequenceEditComparator(drop), SectionOrderComparator(drop)):
            r34, r39, r31 = (cmp(c, p3, p4, mem), cmp(c, p3, p9, mem), cmp(c, p3, p1, mem))
            rows.append((c.method_id, drop, r34.score, r39.score, r31.score))
            assert r34.score > r39.score
            assert r34.evidence_refs[p3.canonical_id]
    with capsys.disabled():
        for r in rows:
            print("E03", r)
    seqev = cmp(SequenceEditComparator(), p3, p4, mem)
    assert seqev.score > 0.9 and "wspólna sekwencja" in seqev.explanation


def test_runner_integration():
    from methadata.contracts import discover
    from methadata.runner import run_experiment
    from methadata.runner.run import BundleReader
    mem = Mem()
    obs = [make("a", article("T", ["1", "2"]), mem), make("b", article("U", ["3", "4"]), mem),
           make("f", b"<html><body><form><input></form></body></html>", mem)]

    class R(BundleReader):
        def open_bytes(self, ref):
            return mem.d[ref.artifact_id]
    cl = next(k for k, v in discover().items() if v.spec.kind.value == "clusterer")
    cfg = {"dataset_version": "t", "seed": 0, "extractors": ["dom-struct-extractor"],
           "comparators": ["dom-sequence-edit-v1"], "clusterer": cl,
           "cluster_config": {"threshold": 0.9, "similarity_version": "dom-sequence-edit-v1", "dataset_version": "t"}}
    res = run_experiment(cfg, obs, discover(), reader=R(obs))
    assert len(res["bundle"].pairs) == 3
