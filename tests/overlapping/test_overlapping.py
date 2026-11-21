import itertools

import pytest

from methadata.clustering.overlapping import (build_axis, build_result, co_membership, explain_pair, get_adapters,
                                              resolve_config, small_family_loss)
from methadata.contracts import (Artifact, ArtifactRef, ArtifactRole, ClusterRun, InputKind, Observation,
                                 PairEvidence, Relation, discover, ids)
from methadata.contracts.interfaces import AdapterKind

CFG = {"similarity_version": "asset-jaccard-v1", "dataset_version": "synthetic-1", "threshold": 0.5, "k": 3}
METHOD = "asset-jaccard-v1"


def obs(name):
    url = f"https://{name}.example.invalid/a"
    oid = ids.observation_id(f"synthetic://{name}", "url_only")
    sha = ids.sha256_bytes(name.encode())
    art = Artifact(artifact_id=ids.artifact_id(oid, "input_url", sha), observation_id=oid, role=ArtifactRole.input_url,
                   sha256=sha, url=url)
    return Observation(observation_id=oid, canonical_id=ids.canonical_id([], url), source_ref=f"synthetic://{name}",
                       input_kind=InputKind.url_only, artifacts=[art], tags=["synthetic"])


def pair(a, b, score, rel=Relation.shared_template):
    ca, cb = sorted((a.canonical_id, b.canonical_id))
    refs = {}
    if score is not None:
        for o in (a, b):
            refs[o.canonical_id] = [ArtifactRef(artifact_id=o.artifacts[0].artifact_id)]
    return PairEvidence(run_id="r", pair=(ca, cb), method_id=METHOD, relation=rel, score=score,
                        evidence_refs=refs, explanation="test")


def matrix(o, table, rel=Relation.shared_template):
    return [pair(o[x], o[y], table.get(x + y, 0.0), rel) for x, y in itertools.combinations(sorted(o), 2)]


def groups(run):
    g = {}
    for a in run.assignments:
        for c in a.cluster_ids:
            g.setdefault(c, set()).add(a.material_id)
    return {frozenset(v) for v in g.values()}


def named(o, *names):
    return frozenset(o[n].canonical_id for n in names)


# C należy do rodziny {A,B,C} (szablon X) i {C,D,E} (szablon Y); A-D itd. nie są powiązane
BOWTIE = {"AB": .9, "AC": .8, "BC": .9, "CD": .8, "CE": .9, "DE": .9}


@pytest.fixture
def five():
    return {n: obs(n) for n in "ABCDE"}


def test_two_justified_memberships(five):
    res = build_result(list(five.values()), matrix(five, BOWTIE), CFG)
    assert isinstance(res.run, ClusterRun)
    assert groups(res.run) == {named(five, *"ABC"), named(five, *"CDE")}
    c = five["C"].canonical_id
    a = next(x for x in res.run.assignments if x.material_id == c)
    assert len(a.cluster_ids) == 2 and a.status.value == "grouped"
    assert res.overlapping == (c,)
    ev = next(m for m in res.materials if m.material_id == c)
    assert len(ev.cliques) == 2 and {len(cl) for _, cl in ev.cliques} == {3}
    # dowody: każda grupa wskazuje wspólną grupę przez C
    assert all(cl.shared_with and cl.shared_with[0][1] == (c,) for cl in res.clusters)
    assert all(e.pair_evidence_id for e in res.run.edges)


def test_co_membership_score(five):
    res = build_result(list(five.values()), matrix(five, BOWTIE), CFG)
    A, B, C, D = (five[n].canonical_id for n in "ABCD")
    assert co_membership(res, A, B) == 1.0
    assert co_membership(res, B, C) == 0.5     # B w 1 grupie, C w 2, wspólna 1
    assert co_membership(res, A, D) == 0.0
    ex = explain_pair(res, B, C)
    assert len(ex["shared_clusters"]) == 1 and ex["direct_edge"].score == .9


def test_k4_removes_triangles(five):
    res = build_result(list(five.values()), matrix(five, BOWTIE), {**CFG, "k": 4})
    assert groups(res.run) == set()
    assert {a.status.value for a in res.run.assignments} == {"singleton"}


def test_triangle_requirement_loses_small_families():
    o = {n: obs(n) for n in "ABCDEFGHIJ"}
    table = {"AB": .9, "AC": .9, "BC": .9,        # trójkąt: zostaje
             "DE": .8,                              # para: znika
             "FG": .7, "GH": .7,                    # ścieżka 3: znika
             "IJ": .6}                              # para: znika
    obs_l = list(o.values())
    loss = small_family_loss(obs_l, matrix(o, table), CFG)
    assert (loss["small_families"], loss["lost"], loss["kept"]) == (4, 3, 1)
    assert named(o, "D", "E") in {frozenset(f) for f in loss["lost_families"]}
    assert small_family_loss(obs_l, matrix(o, table), {**CFG, "k": 2})["lost"] == 0


def test_relation_axis_differs(five):
    pe = matrix(five, BOWTIE) + matrix(five, {"AB": .9, "AC": .9, "BC": .9, "DE": .9, "CD": .9, "CE": .9, "AD": .9,
                                              "AE": .9, "BD": .9, "BE": .9}, Relation.shared_narrative)
    out = build_axis(list(five.values()), pe, {**CFG, "relations": ["shared_template", "shared_narrative"]})
    C = five["C"].canonical_id
    assert len(out["axis"][C]["shared_template"]) == 2
    assert len(out["axis"][C]["shared_narrative"]) == 1


def test_missing_comparison_not_zero_and_threshold(five):
    o = {n: five[n] for n in "ABCD"}
    pe = matrix(o, {"AB": .9, "AC": .9, "BC": .9})
    pe = [p for p in pe if D(o) not in p.pair] + [pair(o["A"], o["D"], None)]
    res = build_result(list(o.values()), pe, CFG)
    st = {a.material_id: a.status.value for a in res.run.assignments}
    assert st[o["D"].canonical_id] == "insufficient_data"
    low = build_result(list(o.values()), matrix(o, {"AB": .4, "AC": .4, "BC": .4}), CFG)
    assert groups(low.run) == set()


def D(o):
    return o["D"].canonical_id


def test_config_requires_versions_and_validation(five):
    with pytest.raises(ValueError):
        resolve_config({"dataset_version": "x"})
    with pytest.raises(ValueError):
        resolve_config({**CFG, "k": 1})
    with pytest.raises(ValueError):
        resolve_config({**CFG, "threshold": 2})


def test_deterministic(five):
    pe = matrix(five, BOWTIE)
    a = build_result(list(five.values()), pe, CFG).run
    b = build_result(list(reversed(list(five.values()))), list(reversed(pe)), CFG).run
    assert a.run_id == b.run_id and a.model_dump() == b.model_dump()


def test_discover_returns_adapter_and_runs(five):
    found = [a for a in discover().values() if a.spec.id == "overlapping-v1"]
    assert len(found) == 1 and found[0].spec.kind is AdapterKind.clusterer
    run = found[0].cluster(list(five.values()), matrix(five, BOWTIE), CFG)
    assert len(groups(run)) == 2
    assert get_adapters()[0].spec.id == "overlapping-v1"
