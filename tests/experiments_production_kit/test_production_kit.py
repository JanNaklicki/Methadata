import pytest

from methadata.contracts import ids
from methadata.contracts.models import Artifact, ArtifactRole, InputKind, Observation
from methadata.contracts.registry import discover
from methadata.devtools.realdata import BACKUP
from methadata.experiments.production_kit import (Corpus, KitExtractor, ProductionKitComparator,
                                                  ProductionKitNoLibComparator, extract_elements, form_signature)
from methadata.experiments.production_kit.report import build_report, collect_features, render


class Mem:
    def __init__(self):
        self.data, self.arts = {}, {}

    def open_bytes(self, ref):
        return self.data[ref.artifact_id]

    def artifact(self, i):
        return self.arts[i]

    def children(self, i):
        return []


KIT_CSS = b".zx-lead{margin:0;padding:4px}.zx-lead .zx-cta{color:#c00;display:flex}@media(max-width:600px){.zx-cta{width:100%}}"
KIT_JS = b"function zxSend(a){var u='https://pay.example.invalid/x';return fetch(u,{method:'POST',body:a}).then(function(r){return r.json()})}"
BOOT_CSS = b".row{display:flex}.col-md-6{width:50%}.btn{padding:6px}.btn-primary{color:#fff}"
JQ_JS = b"(function(w){var jq=function(s){return document.querySelectorAll(s)};w.jQuery=jq;w.$=jq;jq.fn={each:function(f){return f}}})(window);"
FORM = '<form method="post" action="/s"><input name="phone" type="tel"><input name="name" type="text"><button>OK</button></form>'


def page(mem, name, *, kit, extra_class="", uniq=0):
    oid = ids.observation_id("syn://" + name, "full_capture")
    host = f"https://{name}.example.invalid/"
    parts = []
    html = ["<html><head>"]
    if kit:
        html.append('<link rel="stylesheet" href="/kit.css"><script src="/kit.js"></script>')
        parts += [("kit.css", KIT_CSS, "text/css"), ("kit.js", KIT_JS, "application/javascript")]
    html.append('<link rel="stylesheet" href="/css/bootstrap.min.css"><script src="/js/jquery.min.js"></script>')
    parts += [("css/bootstrap.min.css", BOOT_CSS, "text/css"), ("js/jquery.min.js", JQ_JS, "application/javascript")]
    own = f".own{uniq}{{margin:{uniq}px}}".encode()
    parts.append((f"own{uniq}.css", own, "text/css"))
    html.append(f'<link rel="stylesheet" href="/own{uniq}.css"></head><body>')
    html.append(f'<div class="container row btn {"zx-lead" if kit else "u" + str(uniq)} {extra_class} neterror">')
    html.append(FORM if kit else f'<form action="/q"><input name="q{uniq}" type="text"><input name="z{uniq}"></form>')
    html.append("</div></body></html>")
    arts = []
    for fn, data, mt in [("index.html", "".join(html).encode(), "text/html")] + parts:
        role = ArtifactRole.landing_html if fn == "index.html" else ArtifactRole.resource
        sha = ids.sha256_bytes(data)
        a = Artifact(artifact_id=ids.artifact_id(oid, role.value, sha, fn), observation_id=oid, role=role, local_ref=f"{name}/{fn}",
                     sha256=sha, media_type=mt, url=host + (fn if fn != "index.html" else ""), origin=fn)
        arts.append(a)
        mem.arts[a.artifact_id], mem.data[a.artifact_id] = a, data
    return Observation(observation_id=oid, canonical_id="can-" + name, source_ref="syn://" + name,
                       input_kind=InputKind.full_capture, artifacts=arts)


@pytest.fixture(scope="module")
def corpus():
    mem = Mem()
    obs = [page(mem, "a", kit=True, uniq=1), page(mem, "b", kit=True, uniq=2)] + \
          [page(mem, f"c{i}", kit=False, uniq=10 + i) for i in range(6)]
    return mem, obs, collect_features(obs, mem)


def test_discovered():
    ad = discover()
    assert {"kit-extractor", "production-kit-v1", "production-kit-nolib-v1"} <= set(ad)


def test_form_signature_ignores_order_and_requires_fields():
    f1 = {"method": "POST", "fields": [{"tag": "input", "type": "tel", "name": "Phone"}, {"tag": "input", "type": "text", "name": "n"}]}
    f2 = {"method": "post", "fields": list(reversed(f1["fields"]))}
    assert form_signature(f1) == form_signature(f2)
    assert form_signature({"method": "get", "fields": [{"tag": "input", "type": "text", "name": "q"}]}) is None


def test_elements_origin(corpus):
    mem, obs, fm = corpus
    els = extract_elements(obs[0], fm[obs[0].observation_id])
    kinds = {}
    for e in els.values():
        kinds.setdefault((e.kind, e.origin), []).append(e.label)
    assert ("css_sheet", "own") in kinds and ("script", "own") in kinds and ("form", "own") in kinds
    assert ("css_sheet", "library") in kinds and ("script", "library") in kinds
    assert any("neterror" in l for l in kinds[("class", "browser")])
    assert any("zx-lead" in l for l in kinds[("class", "own")])
    assert all(e.refs for e in els.values())


def test_kit_pair_scores_and_filtered_variant(corpus):
    mem, obs, fm = corpus
    c1, c2 = ProductionKitComparator.fit(obs, fm), ProductionKitNoLibComparator.fit(obs, fm)
    a, b, c = obs[0], obs[1], obs[2]
    feats = lambda *os: {o.observation_id: fm[o.observation_id] for o in os}
    ev_all = c1.compare(a, b, feats(a, b), run_id="r")[0]
    ev_own = c2.compare(a, b, feats(a, b), run_id="r")[0]
    assert ev_all.score > 0 and ev_own.score > 0
    assert ev_all.score >= ev_own.score
    assert "df=2/8" in ev_own.explanation and "[library]" not in ev_own.explanation
    assert set(ev_own.evidence_refs) == {"can-a", "can-b"} and ev_own.evidence_refs["can-a"] and ev_own.evidence_refs["can-b"]
    # strona bez zestawu: wspólne są tylko biblioteki (niskie df nie dotyczy, df=8) i klasy ogólne
    ev_ac = c2.compare(a, c, feats(a, c), run_id="r")[0]
    assert ev_ac.score == 0.0


def test_libraries_alone_are_not_rare(corpus):
    mem, obs, fm = corpus
    c = ProductionKitComparator.fit(obs, fm)
    x, y = obs[3], obs[4]
    ev = c.compare(x, y, {x.observation_id: fm[x.observation_id], y.observation_id: fm[y.observation_id]}, run_id="r")[0]
    assert ev.score == 0.0  # jQuery/Bootstrap w całym korpusie: df=N, nie są rzadkie


def test_unfitted_and_empty_are_no_basis(corpus):
    mem, obs, fm = corpus
    ev = ProductionKitComparator().compare(obs[0], obs[1], fm, run_id="r")[0]
    assert ev.score is None
    ev = ProductionKitComparator(Corpus(8, {})).compare(obs[0], obs[1], {}, run_id="r")[0]
    assert ev.score is None and "brak podstaw" in ev.explanation


def test_report_lists_frequency_both_variants(corpus):
    mem, obs, fm = corpus
    rep = build_report(obs, fm, dataset_version="syn")
    p = rep["pairs"][0]
    assert p["pair"] == ["can-a", "can-b"]
    assert len(p["with_libraries"]) >= len(p["without_libraries"]) >= 2
    assert all(e["df"] == 2 and e["N"] == 8 and e["where_a"] and e["where_b"] for e in p["without_libraries"])
    txt = render(rep)
    assert "Bez bibliotek i szablonu przeglądarki" in txt and "df=2/8" in txt


def test_parser_failure_is_status_not_exception():
    mem = Mem()
    o = page(mem, "z", kit=True, uniq=3)
    ex = KitExtractor()

    class Bad:
        def open_bytes(self, ref):
            raise OSError("nie ma")
    f, st = ex.extract(o, reader=Bad())
    assert f == [] and st[0].state.value == "failed"
    f, st = ex.extract(Observation(observation_id=o.observation_id, canonical_id="x", source_ref="x",
                                   input_kind=InputKind.full_capture, artifacts=[o.artifacts[1]]), reader=mem)
    assert st[0].state.value == "missing_input"


@pytest.mark.skipif(not BACKUP.is_dir(), reason="brak katalogu BACKUP")
def test_real_data_report(tmp_path):
    """Próbka rzeczywista (BACKUP): raport ma wspólne rzadkie elementy z częstością i lokalizacją po obu stronach."""
    from methadata.ingest import Store
    from methadata.ingest.__main__ import main as ingest_main
    ingest_main(["import", "--store", str(tmp_path / "st"), str(BACKUP)])
    st = Store(tmp_path / "st")
    obs = st.observations()
    rep = build_report(obs, collect_features(obs, st), top=3, dataset_version="backup-local")
    assert rep["N"] == len(obs) > 2 and rep["pairs"]
    for p in rep["pairs"]:
        assert len(p["without_libraries"]) <= len(p["with_libraries"])
        for e in p["with_libraries"]:
            assert e["df"] <= rep["rare_max_df"] and e["where_a"] and e["where_b"]
