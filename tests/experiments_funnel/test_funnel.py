import json
from pathlib import Path

import pytest

from methadata.contracts import Artifact, ArtifactRef, Bundle, InputKind, Observation, ids
from methadata.experiments.funnel import (FunnelComparator, FunnelExtractor, analyze_html, edit_similarity,
                                          lcs_pairs, lcs_similarity)

BACKUP = Path("/Users/jnaklicki/Documents/Hackathon/Methadata/BACKUP")
LONG = "Lorem ipsum dolor sit amet consectetur. " * 8


class Mem:
    def __init__(self):
        self.d = {}

    def open_bytes(self, ref):
        return self.d[ref.artifact_id]


def make(name, mem, html=None, har=None):
    oid = ids.observation_id(name, "full_capture")
    arts = []
    for role, data, ext in (("landing_html", html, ".html"), ("har", har, ".har")):
        if data is None:
            continue
        sha = ids.sha256_bytes(data)
        a = Artifact(artifact_id=ids.artifact_id(oid, role, sha), observation_id=oid, role=role,
                     local_ref=name + ext, sha256=sha)
        mem.d[a.artifact_id] = data
        arts.append(a)
    return Observation(observation_id=oid, canonical_id="can-" + name, source_ref=name,
                       input_kind=InputKind.full_capture, artifacts=arts)


ARTICLE = f"<html><body><article><h1>T</h1><p>{LONG}</p></article><a href='/x'>Sprawdź teraz</a>"
FORM = "<form action='/s'><input type='email' name='e'><input type='tel'><input type='hidden'><button>Wyślij</button></form>"
PAGE_AF = (ARTICLE + FORM + "</body></html>").encode()
PAGE_A = (ARTICLE + "</body></html>").encode()
PAGE_F = ("<html><body><h1>Zapisz</h1>" + FORM + "</body></html>").encode()


def entry(url, status=200, text=None, mime="text/html", method="GET", rtype="document", loc=None, post=None):
    e = {"request": {"method": method, "url": url, "headers": []},
         "response": {"status": status, "headers": ([{"name": "Location", "value": loc}] if loc else []),
                      "redirectURL": loc or "", "content": {"mimeType": mime, "size": len(text or ""), "text": text}},
         "_resourceType": rtype}
    if post:
        e["request"]["postData"] = {"mimeType": post, "text": "a=1"}
    return e


def har(*entries):
    return json.dumps({"log": {"entries": list(entries)}}).encode()


def feat(o, mem):
    f, st = FunnelExtractor().extract(o, reader=mem)
    assert st[-1].state.value == "ok", st
    return {o.observation_id: f}, f[0].value


def test_stage_analysis_order_and_spans():
    text = PAGE_AF.decode()
    st = analyze_html(text)
    assert [s.kind for s in st] == ["article", "cta", "form"]
    for s in st:
        assert text[s.start:s.end].startswith("<")
    assert "email" in st[2].detail and "hidden" not in st[2].detail
    assert [s.kind for s in analyze_html("<html><body><form role='search'><input></form></body></html>")] == []


def test_static_only_is_incomplete_and_marked_static():
    mem = Mem()
    o = make("a", mem, html=PAGE_AF)
    _, v = feat(o, mem)
    assert [(s["kind"], s["source"]) for s in v["steps"]] == [("article", "static"), ("cta", "static"), ("form", "static")]
    assert v["complete"] is False and "statycznym" in v["incomplete_reason"]
    assert all(s["ref"]["locator"]["kind"] == "text_span" for s in v["steps"])


def test_har_observed_steps_with_sources():
    mem = Mem()
    h = har(entry("https://a.example.invalid/?gclid=SECRET", text=PAGE_A.decode()),
            entry("https://a.example.invalid/go", 302, mime="text/html", loc="https://b.example.invalid/f"),
            entry("https://b.example.invalid/f", text=PAGE_F.decode()),
            entry("https://b.example.invalid/s", 302, method="POST", post="application/x-www-form-urlencoded",
                  loc="https://b.example.invalid/ok"),
            entry("https://cdn.example.invalid/x.js", text="x", mime="application/javascript", rtype="script"))
    o = make("b", mem, har=h)
    _, v = feat(o, mem)
    got = [(s["kind"], s["source"], s["ref"]["locator"].get("index")) for s in v["steps"]]
    assert got == [("article", "observed", 0), ("cta", "static", 0), ("redirect", "observed", 1),
                   ("form", "observed", 2), ("submit", "observed", 3)]
    assert v["complete"] is True and "SECRET" not in json.dumps(v)


def test_missing_further_step_gives_incomplete_sequence():
    mem = Mem()
    h = har(entry("https://a.example.invalid/", text=PAGE_A.decode()))
    _, v = feat(make("c", mem, har=h), mem)
    assert v["complete"] is False and "nie zaobserwowano dalszego kroku" in v["incomplete_reason"]


def test_html_with_har_uses_text_spans_and_observed_ref():
    mem = Mem()
    h = har(entry("https://a.example.invalid/", text=PAGE_AF.decode()))
    _, v = feat(make("d", mem, html=PAGE_AF, har=h), mem)
    kinds = {s["kind"]: s for s in v["steps"]}
    assert kinds["form"]["source"] == "observed" and kinds["form"]["ref"]["locator"]["kind"] == "text_span"
    assert kinds["form"]["observed_ref"]["locator"]["kind"] == "har_entry" and kinds["cta"]["source"] == "static"
    assert v["complete"] is True


def test_missing_input_and_corrupt_har():
    mem = Mem()
    e = Observation(observation_id="obs-x", canonical_id="can-x", source_ref="x", input_kind=InputKind.full_capture,
                    artifacts=[Artifact(artifact_id="art-x", observation_id="obs-x", role="other", local_ref="x",
                                        sha256=ids.sha256_bytes(b"x"))])
    f, st = FunnelExtractor().extract(e, reader=mem)
    assert f == [] and st[0].state.value == "missing_input"
    o = make("bad", mem, har=b"{nie json")
    f, st = FunnelExtractor().extract(o, reader=mem)
    assert f == [] and st[0].state.value == "failed"


def two(mem):
    full = make("full", mem, har=har(entry("https://a.example.invalid/", text=PAGE_A.decode()),
                                     entry("https://b.example.invalid/f", text=PAGE_F.decode())))
    cut = make("cut", mem, har=har(entry("https://c.example.invalid/", text=PAGE_A.decode())))
    return full, cut


def test_full_vs_truncated_results_and_source_refs():
    mem = Mem()
    full, cut = two(mem)
    fm = {**feat(full, mem)[0], **feat(cut, mem)[0]}
    ef = FunnelComparator("edit", "full").compare(full, cut, fm, run_id="r")[0]
    et = FunnelComparator("edit", "truncated").compare(full, cut, fm, run_id="r")[0]
    lf = FunnelComparator("lcs", "full").compare(full, cut, fm, run_id="r")[0]
    # full: article>cta>form vs article>cta ; ucięty: wspólny prefiks article>cta
    assert ef.score == pytest.approx(2 / 3) and et.score == 1.0 and lf.score == pytest.approx(2 / 3)
    assert ef.method_id != et.method_id and ef.pair_evidence_id != et.pair_evidence_id
    assert "niepełna" in ef.explanation and "*" in ef.explanation
    for ev in (ef, et, lf):
        for cid in ev.pair:
            assert ev.evidence_refs[cid] and all(isinstance(r, ArtifactRef) for r in ev.evidence_refs[cid])
    Bundle(observations=[full, cut], pairs=[ef, et, lf])  # odwołania do artefaktów poprawne
    # symetria
    assert FunnelComparator().compare(cut, full, fm, run_id="r")[0].pair_evidence_id == ef.pair_evidence_id


def test_none_without_features_and_algorithms():
    mem = Mem()
    full, cut = two(mem)
    ev = FunnelComparator().compare(full, cut, {full.observation_id: [], cut.observation_id: []}, run_id="r")[0]
    assert ev.score is None and "brak podstaw" in ev.explanation
    assert edit_similarity(list("abc"), list("abd")) == pytest.approx(2 / 3)
    assert lcs_pairs(list("abcd"), list("acd")) == [(0, 0), (2, 1), (3, 2)]
    assert lcs_similarity([], ["a"]) is None


def test_discover_and_runner_integration():
    from methadata.contracts import discover
    from methadata.contracts.interfaces import check_adapter
    from methadata.runner import run_experiment
    from methadata.runner.run import BundleReader
    found = discover()
    for i in ("funnel-extractor", "funnel-edit-full-v1", "funnel-edit-truncated-v1", "funnel-lcs-full-v1",
              "funnel-lcs-truncated-v1"):
        check_adapter(found[i])
    mem = Mem()
    full, cut = two(mem)
    other = make("o", mem, html=b"<html><body><p>x</p></body></html>")
    obs = [full, cut, other]

    class R(BundleReader):
        def open_bytes(self, ref):
            return mem.d[ref.artifact_id]
    cl = next(k for k, v in found.items() if v.spec.kind.value == "clusterer")
    cfg = {"dataset_version": "t", "seed": 0, "extractors": ["funnel-extractor"],
           "comparators": ["funnel-edit-truncated-v1"], "clusterer": cl,
           "cluster_config": {"threshold": 0.9, "similarity_version": "funnel-edit-truncated-v1", "dataset_version": "t"}}
    res = run_experiment(cfg, obs, found, reader=R(obs))
    assert len(res["bundle"].pairs) == 3


# ---- dane rzeczywiste (pomijane bez BACKUP)
def real(p):
    d = BACKUP / p
    hars, htmls = list(d.glob("*.har")), [x for x in d.glob("*.htm*") if x.is_file()]
    if not hars or not htmls:
        pytest.skip("brak katalogu BACKUP")
    return htmls[0].read_bytes(), hars[0].read_bytes()


def test_real_two_sequences(capsys):
    mem = Mem()
    obs = {p: make(p, mem, *real(p)) for p in ("p3", "p4", "p9", "p10", "p13")}
    vals = {p: feat(o, mem) for p, o in obs.items()}
    with capsys.disabled():
        for p, (_, v) in vals.items():
            print("E13", p, [(s["kind"], s["source"][:3]) for s in v["steps"]], "complete=", v["complete"], v["incomplete_reason"])
    fm = {}
    for f, _ in vals.values():
        fm.update(f)
    for p in ("p3", "p4", "p10", "p13"):
        v = vals[p][1]
        assert v["steps"] and all(s["ref"]["artifact_id"] for s in v["steps"])
    assert [s["kind"] for s in vals["p3"][1]["steps"]] == ["article", "cta"] and not vals["p3"][1]["complete"]
    assert vals["p13"][1]["complete"] and [s["kind"] for s in vals["p13"][1]["steps"]] == ["article", "form"]
    assert vals["p9"][1]["steps"] == []  # strona z odtwarzaczem wideo: brak etapów (przypadek zawodności)
    a, b = obs["p3"], obs["p4"]
    for md in ("full", "truncated"):
        ev = FunnelComparator("edit", md).compare(a, b, fm, run_id="r")[0]
        with capsys.disabled():
            print("E13 p3-p4", md, ev.score, "| p13-p10", FunnelComparator("edit", md).compare(obs["p13"], obs["p10"], fm, run_id="r")[0].score)
        assert ev.score == 1.0 and ev.evidence_refs[a.canonical_id] and ev.evidence_refs[b.canonical_id]
    assert FunnelComparator().compare(a, obs["p9"], fm, run_id="r")[0].score is None
