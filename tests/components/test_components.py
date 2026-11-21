import itertools
import random

import pytest

from methadata.clustering.components import ComponentsClusterer, build_result, sweep
from methadata.contracts import (Artifact, ArtifactRef, ArtifactRole, Bundle, ClusterRun, InputKind, Observation,
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


def pair(a, b, score, method=METHOD, relation=Relation.shared_template):
    ca, cb = sorted((a.canonical_id, b.canonical_id))
    refs = {}
    if score is not None:
        for o in (a, b):
            refs[o.canonical_id] = [ArtifactRef(artifact_id=o.artifacts[0].artifact_id)]
    return PairEvidence(run_id="r", pair=(ca, cb), method_id=method, relation=relation, score=score,
                        evidence_refs=refs, explanation="test")


def groups(run):
    g = {}
    for a in run.assignments:
        for c in a.cluster_ids:
            g.setdefault(c, set()).add(a.material_id)
    return {frozenset(v) for v in g.values()}


@pytest.fixture
def four():
    return {n: obs(n) for n in "ABCD"}


def chain(four, bridge=0.3):
    o = four
    return [pair(o["A"], o["B"], 0.9), pair(o["B"], o["C"], bridge), pair(o["C"], o["D"], 0.9),
            pair(o["A"], o["C"], 0.0), pair(o["A"], o["D"], 0.0), pair(o["B"], o["D"], 0.0)]


def test_four_nodes_lowering_threshold_merges_groups(four):
    o, ps = four, chain(four)
    hi = build_result(list(o.values()), ps, CFG).run
    lo = build_result(list(o.values()), ps, {**CFG, "threshold": 0.3}).run
    c = lambda n: o[n].canonical_id  # noqa: E731
    assert groups(hi) == {frozenset({c("A"), c("B")}), frozenset({c("C"), c("D")})}
    assert groups(lo) == {frozenset(c(n) for n in "ABCD")}  # próg inkluzywny: score == threshold łączy


def test_bridge_is_reported_with_evidence(four):
    res = build_result(list(four.values()), chain(four), {**CFG, "threshold": 0.3})
    (cl,) = res.clusters
    assert len(cl.bridges) == 3  # w łańcuchu każda krawędź jest mostem
    assert cl.weakest.weight == 0.3
    assert all(e.pair_evidence_id.startswith("pev-") for e in res.run.edges)
    hi = build_result(list(four.values()), chain(four), CFG)
    assert [e.weight for e in hi.below_threshold if e.weight > 0] == [0.3]  # dowód odciętej krawędzi


def test_sweep_notes_single_bridge(four):
    steps = sweep(list(four.values()), chain(four), CFG)
    assert [s["threshold"] for s in steps] == [0.9, 0.3]
    assert steps[0]["merges"] == [] or all(not m["single_bridge"] for m in steps[0]["merges"])
    (m,) = steps[1]["merges"]
    assert m["single_bridge"] and len(m["joining_edges"]) == 1 and m["joining_edges"][0][2] == 0.3


def test_isolated_and_missing_comparison(four):
    o = four
    ps = [pair(o["A"], o["B"], 0.9), pair(o["A"], o["C"], 0.1), pair(o["B"], o["C"], 0.1),
          pair(o["A"], o["D"], None), pair(o["B"], o["D"], None), pair(o["C"], o["D"], None)]
    run = build_result(list(o.values()), ps, CFG).run
    st = {a.material_id: a.status.value for a in run.assignments}
    assert st[o["C"].canonical_id] == "singleton"           # porównano, ale poniżej progu
    assert st[o["D"].canonical_id] == "insufficient_data"   # brak wykonalnego porównania != singleton
    assert st[o["A"].canonical_id] == "grouped"


def test_order_independent_and_valid_contract(four):
    o, ps = list(four.values()), chain(four)
    base = build_result(o, ps, {**CFG, "threshold": 0.3}).run
    for _ in range(5):
        random.shuffle(o), random.shuffle(ps)
        assert build_result(o, ps, {**CFG, "threshold": 0.3}).run == base
    ClusterRun.model_validate(base.model_dump(mode="json"))
    Bundle(observations=list(four.values()), pairs=chain(four), runs=[base])  # krawędzie wskazują istniejące dowody


def test_config_requires_versions_and_valid_threshold(four):
    for key in ("similarity_version", "dataset_version", "threshold"):
        with pytest.raises(ValueError):
            build_result(list(four.values()), [], {k: v for k, v in CFG.items() if k != key})
    for bad in (0, 1.5, True):
        with pytest.raises(ValueError):
            build_result(list(four.values()), [], {**CFG, "threshold": bad})


def test_mixed_methods_need_explicit_method(four):
    o = four
    ps = [pair(o["A"], o["B"], 0.9), pair(o["A"], o["B"], 0.2, method="other")]
    with pytest.raises(ValueError):
        build_result(list(o.values()), ps, CFG)
    run = build_result(list(o.values()), ps, {**CFG, "method_id": "other"}).run
    assert run.edges == []


def test_other_relation_ignored(four):
    o = four
    ps = [pair(o["A"], o["B"], 0.9, relation=Relation.shared_narrative)]
    assert build_result(list(o.values()), ps, CFG).run.edges == []


def test_baseline_measurement_section1():
    """Wartości z §1 PLAN_KLASTROWANIA (syntetyczne węzły, bez danych źródłowych)."""
    names = ["p2", "p7", "p15", "p8", "p11", "p3", "p4"] + ["x1", "x2", "x3", "x4", "x5", "x6", "x7"]
    o = {n: obs(n) for n in names}
    fam = ["p2", "p7", "p15"]
    sc = {frozenset(p): 1.0 for p in itertools.combinations(fam, 2)}
    sc[frozenset(("p8", "p11"))] = 0.543
    sc[frozenset(("p3", "p4"))] = 0.222
    for x in ("p3", "p4"):
        for y in fam[:3]:
            sc[frozenset((x, y))] = 0.136
    ps = [pair(o[a], o[b], sc.get(frozenset((a, b)), 0.0)) for a, b in itertools.combinations(names, 2)]
    assert len(ps) == 91
    cid = lambda *n: frozenset(o[x].canonical_id for x in n)  # noqa: E731
    run = build_result(list(o.values()), ps, CFG).run
    assert groups(run) == {cid("p2", "p7", "p15"), cid("p8", "p11")}
    assert sum(a.status.value == "singleton" for a in run.assignments) == 9
    steps = {s["threshold"]: s for s in sweep(list(o.values()), ps, CFG)}
    assert set(steps) == {1.0, 0.543, 0.222, 0.136}
    assert cid("p3", "p4") in set(map(lambda g: frozenset(g), steps[0.222]["groups"]))
    (m,) = steps[0.136]["merges"]
    # p3/p4 dołączają do rodziny dopiero przy 0,136 i robią to sześcioma krawędziami naraz, nie pojedynczym mostem
    assert len(m["joining_edges"]) == 6 and not m["single_bridge"]


def test_adapter_discovery():
    found = discover(AdapterKind.clusterer)
    assert isinstance(found["components-threshold-v1"], ComponentsClusterer)
