import itertools
import random

import pytest

from methadata.clustering.linkage import build_result, compare_with_components, get_adapters
from methadata.contracts import (Artifact, ArtifactRef, ArtifactRole, ClusterRun, InputKind, Observation,
                                 PairEvidence, Relation, discover, ids)
from methadata.contracts.interfaces import AdapterKind

CFG = {"threshold": 0.5, "similarity_version": "asset-jaccard-v1", "dataset_version": "synthetic-1"}
METHOD = "asset-jaccard-v1"


def obs(name):
    url = f"https://{name}.example.invalid/a"
    oid = ids.observation_id(f"synthetic://{name}", "url_only")
    sha = ids.sha256_bytes(name.encode())
    art = Artifact(artifact_id=ids.artifact_id(oid, "input_url", sha), observation_id=oid, role=ArtifactRole.input_url,
                   sha256=sha, url=url)
    return Observation(observation_id=oid, canonical_id=ids.canonical_id([], url), source_ref=f"synthetic://{name}",
                       input_kind=InputKind.url_only, artifacts=[art], tags=["synthetic"])


def pair(a, b, score):
    ca, cb = sorted((a.canonical_id, b.canonical_id))
    refs = {}
    if score is not None:
        for o in (a, b):
            refs[o.canonical_id] = [ArtifactRef(artifact_id=o.artifacts[0].artifact_id)]
    return PairEvidence(run_id="r", pair=(ca, cb), method_id=METHOD, relation=Relation.shared_template, score=score,
                        evidence_refs=refs, explanation="test")


def matrix(o, table, default=0.0):
    """table: {"AB": 0.9, ...}; brakujące pary = default (None => porównanie niewykonane)."""
    out = []
    for x, y in itertools.combinations(sorted(o), 2):
        out.append(pair(o[x], o[y], table.get(x + y, default)))
    return out


def groups(run):
    g = {}
    for a in run.assignments:
        for c in a.cluster_ids:
            g.setdefault(c, set()).add(a.material_id)
    return {frozenset(v) for v in g.values()}


def named(o, *names):
    return frozenset(o[n].canonical_id for n in names)


@pytest.fixture
def four():
    return {n: obs(n) for n in "ABCD"}


BRIDGE = {"AB": 0.9, "CD": 0.9, "BC": 0.6}  # A-B-C-D, pozostałe pary 0


@pytest.mark.parametrize("linkage", ["average", "complete"])
def test_bridge_does_not_merge_groups_while_single_link_does(four, linkage):
    o, ps = four, matrix(four, BRIDGE)
    res = build_result(list(o.values()), ps, CFG, linkage=linkage)
    assert groups(res.run) == {named(o, "A", "B"), named(o, "C", "D")}
    comp = compare_with_components(list(o.values()), ps, CFG)
    assert len(comp["components"]) == 1 and len(comp["components"][0]) == 4  # K01: most skleja wszystko
    assert comp[linkage]["components_groups_split"] == comp["components"]
    # raport: próg cięcia i łączenia, luka wokół progu
    assert res.cut_threshold == 0.5 and res.cut_distance == pytest.approx(0.5)
    assert res.lowest_performed_merge == pytest.approx(0.9)
    assert res.highest_rejected_merge < 0.5


def test_average_vs_complete_differ_on_partial_link(four):
    o = four  # A-B 0.9, C-D 0.9; między parami: AC .8 BC .8 AD .2 BD .2 -> average .5, complete .2
    ps = matrix(o, {"AB": 0.9, "CD": 0.9, "AC": 0.8, "BC": 0.8, "AD": 0.2, "BD": 0.2})
    avg = build_result(list(o.values()), ps, CFG, linkage="average")
    com = build_result(list(o.values()), ps, CFG, linkage="complete")
    assert groups(avg.run) == {named(o, *"ABCD")}  # 0.5 >= 0.5, próg inkluzywny
    assert groups(com.run) == {named(o, "A", "B"), named(o, "C", "D")}
    assert avg.dendrogram[-1].similarity == pytest.approx(0.5)
    assert com.dendrogram[-1].similarity == pytest.approx(0.2) and not com.dendrogram[-1].performed


def test_family_split_risk_deterministic():
    # konstrukcja jednoznaczna: wariant V dostaje wyższy score z X niż pary rodziny -> scala się pierwszy
    o = {n: obs(n) for n in "VXYZ"}
    ps = matrix(o, {"XY": 0.8, "XZ": 0.8, "YZ": 0.8, "VX": 0.95, "VY": 0.2, "VZ": 0.2})
    com = build_result(list(o.values()), ps, CFG, linkage="complete")
    avg = build_result(list(o.values()), ps, CFG, linkage="average")
    fam = named(o, "X", "Y", "Z")
    assert fam not in groups(com.run)                # rodzina rozbita (VX, potem YZ; łączenie = 0.2)
    assert named(o, "V", "X") in groups(com.run) and named(o, "Y", "Z") in groups(com.run)
    # average: {V,X} vs {Y,Z} = (0.2+0.2+0.8+0.8)/4 = 0.5 -> przy progu 0.5 wszystko razem, przy 0.55 też rozbija
    assert groups(avg.run) == {named(o, *"VXYZ")}
    avg2 = build_result(list(o.values()), ps, {**CFG, "threshold": 0.55}, linkage="average")
    assert named(o, "V", "X") in groups(avg2.run)
    # bez wariantu rodzina jest nienaruszona
    ps3 = [p for p in ps if o["V"].canonical_id not in p.pair]
    res = build_result([o[n] for n in "XYZ"], ps3, CFG, linkage="complete")
    assert groups(res.run) == {fam}


def test_exclusions_with_reasons_and_statuses(four):
    o = four
    # D: żadnych porównań (score=None); C: brak porównania z A (niekompletna macierz)
    table = {"AB": 0.9, "BC": 0.8, "AC": None, "AD": None, "BD": None, "CD": None}
    res = build_result(list(o.values()), matrix(o, table), CFG, linkage="average")
    reasons = {e.material_id: e.reason for e in res.excluded}
    assert reasons[o["D"].canonical_id] == "no_comparisons"
    assert len(res.excluded) == 2
    other = [e for e in res.excluded if e.reason == "incomplete_matrix"][0]
    assert other.material_id in (o["A"].canonical_id, o["C"].canonical_id) and other.detail
    status = {a.material_id: a.status.value for a in res.run.assignments}
    assert status[o["D"].canonical_id] == "insufficient_data" and status[other.material_id] == "insufficient_data"
    assert groups(res.run) == {frozenset({o["B"].canonical_id,
                                          next(o[n].canonical_id for n in "AC" if o[n].canonical_id != other.material_id)})}


def test_evidence_and_run_validity(four):
    o, ps = four, matrix(four, BRIDGE)
    res = build_result(list(o.values()), ps, CFG, linkage="complete")
    assert ClusterRun.model_validate(res.run.model_dump(mode="json"))
    pids = {p.pair_evidence_id for p in ps}
    assert res.run.edges and all(e.pair_evidence_id in pids for e in res.run.edges)
    for c in res.clusters:
        assert c.merges and c.weakest and c.merge_similarity >= 0.5
        assert all(e.pair_evidence_id in pids for m in c.merges for e in m.edges)
    assert res.run.config["linkage"] == "complete" and res.run.config["similarity_version"] == "asset-jaccard-v1"
    assert res.run.dataset_version == "synthetic-1"
    assert len(res.dendrogram) == 3  # n-1 scaleń, także odrzucone


def test_config_requires_versions_and_valid_threshold(four):
    ps = matrix(four, BRIDGE)
    for key in ("similarity_version", "dataset_version", "threshold"):
        bad = {k: v for k, v in CFG.items() if k != key}
        with pytest.raises(ValueError):
            build_result(list(four.values()), ps, bad)
    with pytest.raises(ValueError):
        build_result(list(four.values()), ps, {**CFG, "threshold": 1.5})


@pytest.mark.parametrize("linkage", ["average", "complete"])
def test_order_independence(linkage):
    o = {n: obs(n) for n in "ABCDEF"}
    rnd = random.Random(1)
    table = {a + b: round(rnd.choice([0.2, 0.5, 0.5, 0.8, 0.9]), 1) for a, b in itertools.combinations("ABCDEF", 2)}
    ps = matrix(o, table)
    base = build_result(list(o.values()), ps, CFG, linkage=linkage).run
    for s in range(5):
        a, b = list(o.values()), list(ps)
        random.Random(s).shuffle(a)
        random.Random(s + 9).shuffle(b)
        assert build_result(a, b, CFG, linkage=linkage).run == base


def test_threshold_one_and_ties_inclusive(four):
    o = four
    ps = matrix(o, {"AB": 1.0, "CD": 0.7})
    res = build_result(list(o.values()), ps, {**CFG, "threshold": 1.0}, linkage="average")
    assert groups(res.run) == {named(o, "A", "B")}  # float: 1 - (1 - 1.0) >= 1


def test_entry_points_discovered():
    found = set(discover(AdapterKind.clusterer))
    assert {"linkage-average-v1", "linkage-complete-v1"} <= found
    assert len(get_adapters()) == 2
