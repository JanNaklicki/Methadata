"""Generuje syntetyczne próbki kontraktu (bez danych źródłowych): python scripts/make_contract_fixtures.py"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from methadata.contracts import ids
from methadata.contracts.models import *  # noqa: F403

OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "contract"


def sha(label: str) -> str:
    return hashlib.sha256(f"synthetic:{label}".encode()).hexdigest()


def art(obs, role, label, local_ref, *, parent=None, rel=None, url=None, origin=None, media=None):
    s = sha(label)
    return Artifact(artifact_id=ids.artifact_id(obs, role, s, origin or local_ref or url or ""), observation_id=obs,
                    role=role, local_ref=local_ref, sha256=s, size=1000, media_type=media, url=url,
                    parent=ArtifactRef(artifact_id=parent[0], locator=parent[1]) if parent else None,
                    parent_relation=rel, origin=origin)


def observation(source_ref, kind, build, content_labels, url=None):
    oid = ids.observation_id(source_ref, kind)
    arts = build(oid)
    cid = ids.canonical_id([(a.role.value, a.sha256) for a in arts], url=url)
    return Observation(observation_id=oid, canonical_id=cid, source_ref=source_ref, input_kind=kind,
                       artifacts=arts, capture_time="2026-10-08T09:09:40Z", tags=["synthetic"])


def full(source_ref, content="A"):
    def build(oid):
        html = art(oid, "landing_html", f"html-{content}", f"{oid}/page.html", media="text/html")
        har = art(oid, "har", f"har-{content}", f"{oid}/capture.har")
        shot = art(oid, "landing_screenshot", f"shot-{content}", f"{oid}/screenshot.png")
        hero = art(oid, "resource", "hero-shared", f"{oid}/res/hero.webp", parent=(html.artifact_id, TextSpan(start=120, end=190)),
                   rel="referenced_by", origin="res/hero.webp")
        body = art(oid, "resource", f"inline-{content}", f"{oid}/res/body.js", parent=(har.artifact_id, HarEntry(index=3)),
                   rel="har_response", origin="har:3")
        return [html, har, shot, hero, body]
    return observation(source_ref, "full_capture", build, content)


def feat(obs: Observation, kind, value, family="asset", hero=True):
    hero_art = next(a for a in obs.artifacts if a.local_ref and a.local_ref.endswith("hero.webp"))
    return Feature(feature_id=ids.feature_id(obs.observation_id, family, kind, "demo-1", value), observation_id=obs.observation_id,
                   family=family, kind=kind, value_type="string", value=value, extractor_version="demo-1",
                   evidence_refs=[ArtifactRef(artifact_id=hero_art.artifact_id)])


def pair(run, a: Observation, b: Observation, method, relation, score, text):
    x, y = sorted([a, b], key=lambda o: o.canonical_id)
    hero = lambda o: [ArtifactRef(artifact_id=next(z.artifact_id for z in o.artifacts if z.local_ref and z.local_ref.endswith("hero.webp")))]
    refs = {x.canonical_id: hero(x), y.canonical_id: hero(y)} if score is not None else {}
    return PairEvidence(run_id=run, pair=(x.canonical_id, y.canonical_id), method_id=method, relation=relation,
                        score=score, evidence_refs=refs, explanation=text)


def dump(name, obj):
    p = OUT / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj.model_dump(mode="json") if hasattr(obj, "model_dump") else obj, indent=2,
                            ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def main():
    # 1. kompletny materiał
    a = full("synthetic://contract/ad-001")
    dump("complete.json", Bundle(observations=[a], features=[feat(a, "image_sha256", sha("hero-shared"))],
         statuses=[ExtractionStatus(observation_id=a.observation_id, artifact_id=x.artifact_id, extractor_version="demo-1", state="ok")
                   for x in a.artifacts[:3]]))
    # 2. sam URL
    uid = ids.observation_id("synthetic://contract/url-001", "url_only")
    url = "https://example.invalid/oferta?gclid=SECRET123&gad_source=5&utm=a"
    ua = Artifact(artifact_id=ids.artifact_id(uid, "input_url", sha(url), url), observation_id=uid, role="input_url",
                  sha256=sha(url), url=url)
    u = Observation(observation_id=uid, canonical_id=ids.canonical_id([], url=url), source_ref="synthetic://contract/url-001",
                    input_kind="url_only", artifacts=[ua], tags=["synthetic"])
    ust = ExtractionStatus(observation_id=uid, artifact_id=ua.artifact_id, extractor_version="demo-1", state="missing_input",
                           detail="brak zapisu strony: porównania treści niewykonalne")
    dump("url_only.json", Bundle(observations=[u], statuses=[ust]))
    # 3. duplikat: te same bajty, inne źródło
    d1, d2 = full("synthetic://contract/dup-1", "D"), full("synthetic://contract/dup-2", "D")
    assert d1.canonical_id == d2.canonical_id and d1.observation_id != d2.observation_id
    dump("duplicate.json", Bundle(observations=[d1, d2]))
    # 4. brakujący zasób: strona bez HAR i zrzutu + porównanie niewykonalne
    mid = ids.observation_id("synthetic://contract/missing-1", "full_capture")
    html = art(mid, "landing_html", "html-M", f"{mid}/page.html", media="text/html")
    m = Observation(observation_id=mid, canonical_id=ids.canonical_id([("landing_html", html.sha256)]), source_ref="synthetic://contract/missing-1",
                    input_kind="full_capture", artifacts=[html], tags=["synthetic"])
    sts = [ExtractionStatus(observation_id=mid, extractor_version="demo-1", state="missing_input", detail="brak HAR"),
           ExtractionStatus(observation_id=mid, artifact_id=html.artifact_id, extractor_version="demo-1", state="ok")]
    b = full("synthetic://contract/ad-002", "B")
    pe = PairEvidence(run_id="run-missing", pair=tuple(sorted([m.canonical_id, b.canonical_id])), method_id="asset-jaccard-v1",
                      relation="shared_template", score=None, explanation="Porównanie niewykonalne: brak zasobów po jednej stronie.")
    dump("missing_resource.json", Bundle(observations=[m, b], statuses=sts, pairs=[pe]))
    # 5. para + ClusterRun do budowy UI
    c = full("synthetic://contract/ad-003", "C")
    lone, = [full("synthetic://contract/ad-004", "Z")]
    run = "demo-run-1"
    p1 = pair(run, a, c, "asset-jaccard-v1", "shared_template", 1.0, "1 z 1 zbadanych grafik ma identyczną zawartość (próbka syntetyczna).")
    p2 = pair(run, a, b, "asset-jaccard-v1", "shared_template", 0.9, "Wspólna grafika otwierająca (próbka syntetyczna).")
    cr = ClusterRun(run_id=run, dataset_version="synthetic-contract-1", method_id="threshold-components", method_version="1", config={"threshold": 0.5}, seed=0,
                    relation="shared_template",
                    assignments=[Assignment(material_id=a.canonical_id, cluster_ids=["c1"], status="grouped"),
                                 Assignment(material_id=b.canonical_id, cluster_ids=["c1"], status="grouped"),
                                 Assignment(material_id=c.canonical_id, cluster_ids=["c1"], status="grouped"),
                                 Assignment(material_id=lone.canonical_id, status="singleton")],
                    edges=[Edge(source=p.pair[0], target=p.pair[1], relation=p.relation, pair_evidence_id=p.pair_evidence_id, weight=p.score) for p in (p1, p2)])
    dump("ui_demo.json", Bundle(observations=[a, b, c, lone], features=[feat(o, "image_sha256", sha("hero-shared")) for o in (a, b, c)],
                                pairs=[p1, p2], runs=[cr]))
    # 6. przypadki wadliwe: (nazwa -> rekord, oczekiwany fragment błędu)
    inv = OUT / "invalid"
    good = json.loads((OUT / "ui_demo.json").read_text())

    def bad(name, mutate, record="bundle"):
        doc = json.loads(json.dumps(good)); mutate(doc)
        dump(f"invalid/{name}.json", doc)

    bad("score_above_one", lambda d: d["pairs"][0].__setitem__("score", 1.5))
    bad("score_negative", lambda d: d["pairs"][0].__setitem__("score", -0.1))
    bad("dangling_artifact_ref", lambda d: d["pairs"][0]["evidence_refs"][d["pairs"][0]["pair"][0]][0].__setitem__("artifact_id", "art-doesnotexist"))
    bad("bad_text_span", lambda d: d["observations"][0]["artifacts"][3]["parent"].__setitem__("locator", {"kind": "text_span", "start": 9, "end": 3}))
    bad("evidence_for_foreign_material", lambda d: d["pairs"][0]["evidence_refs"].__setitem__("can-zzz", []))
    bad("edge_without_pair_evidence", lambda d: d["runs"][0]["edges"][0].__setitem__("pair_evidence_id", "pev-missing"))
    bad("unmasked_display_url", lambda d: None)  # zastępowane niżej
    (inv / "unmasked_display_url.json").unlink()
    udoc = json.loads((OUT / "url_only.json").read_text())
    udoc["observations"][0]["artifacts"][0]["display_url"] = udoc["observations"][0]["artifacts"][0]["url"]
    dump("invalid/unmasked_display_url.json", udoc)


if __name__ == "__main__":
    main()
