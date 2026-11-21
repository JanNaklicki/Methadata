import itertools
import random

import pytest

from methadata.clustering.constrained import (ConstraintConflict, build_result, evaluate_hidden, find_conflicts,
                                              get_adapters, hidden_pairs, parse_constraints)
from methadata.evaluation import Reference
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


def con(o, a, b, kind, **kw):
    return {"a": o[a].canonical_id, "b": o[b].canonical_id, "kind": kind, **kw}


def cfg(o, *cons, **kw):
    return {**CFG, "constraints": list(cons), **kw}


@pytest.fixture
def three():
    return {n: obs(n) for n in "ABC"}


BRIDGE = {"AB": 0.9, "BC": 0.9, "AC": 0.6}


def test_cannot_link_blocks_bridge_a_b_c(three):
    o, ps = three, matrix(three, BRIDGE)
    free = build_result(list(o.values()), ps, cfg(o, con(o, "A", "B", "cannot", relation="shared_narrative")))
    assert groups(free.run) == {named(o, *"ABC")}                 # ograniczenie innej relacji nie działa
    assert len(free.ignored_constraints) == 1 and not free.blocked
    res = build_result(list(o.values()), ps, cfg(o, con(o, "A", "C", "cannot", analyst="x", note="most")))
    assert groups(res.run) == {named(o, "A", "B")}                # A-B pierwsze (remis: leksykograficznie)
    assert len(res.blocked) == 1
    b = res.blocked[0]
    assert set(b.left + b.right) == {o[n].canonical_id for n in "ABC"}
    assert b.similarity == pytest.approx(0.75) and b.constraints[0].kind == "cannot"
    cl = res.clusters[0]
    assert cl.merges[0].basis == "similarity" and cl.merges[0].edges
    assert cl.separated_by[0].note == "most"
    assert {a.material_id: a.status.value for a in res.run.assignments}[o["C"].canonical_id] == "singleton"
    assert res.run.config["constraints"][0]["kind"] == "cannot" and res.run.config["similarity_version"]


def test_must_link_forces_merge_below_threshold(three):
    o = three
    ps = matrix(o, {"AB": 0.9, "BC": 0.1, "AC": 0.1})
    res = build_result(list(o.values()), ps, cfg(o, con(o, "B", "C", "must", analyst="a")))
    assert groups(res.run) == {named(o, *"ABC")}
    forced = [m for m in res.dendrogram if m.basis == "must_link"]
    assert forced and forced[0].constraints[0].pair == tuple(sorted((o["B"].canonical_id, o["C"].canonical_id)))
    assert res.clusters[0].constraints


def test_must_link_without_pair_evidence_still_groups(three):
    o = three
    ps = [p for p in matrix(o, {"AB": 0.9}) if o["C"].canonical_id not in p.pair]
    res = build_result(list(o.values()), ps, cfg(o, con(o, "B", "C", "must")))
    assert named(o, "B", "C") <= set().union(*groups(res.run)) and groups(res.run) == {named(o, *"ABC")}


@pytest.mark.parametrize("cons", [
    lambda o: [con(o, "A", "B", "must"), con(o, "B", "A", "cannot")],
    lambda o: [con(o, "A", "B", "must"), con(o, "B", "C", "must"), con(o, "A", "C", "cannot")],
])
def test_conflicting_constraints_reported(three, cons):
    o = three
    with pytest.raises(ConstraintConflict) as e:
        build_result(list(o.values()), matrix(o, BRIDGE), cfg(o, *cons(o)))
    assert e.value.conflicts and "oznaczona" in e.value.conflicts[0]["detail"] or "domknięciu" in e.value.conflicts[0]["detail"]


def test_conflict_is_per_relation(three):
    o = three
    cs = parse_constraints([con(o, "A", "B", "must"), con(o, "A", "B", "cannot", relation="shared_narrative")],
                           "shared_template")
    assert find_conflicts(cs) == []


def test_requires_explicit_constraints_and_versions(three):
    o, ps = three, matrix(three, BRIDGE)
    for bad in (None, [], "x"):
        with pytest.raises(ValueError, match="constraints"):
            build_result(list(o.values()), ps, {**CFG, "constraints": bad})
    with pytest.raises(ValueError, match="dataset_version"):
        build_result(list(o.values()), ps, {"threshold": 0.5, "similarity_version": "v",
                                            "constraints": [con(o, "A", "B", "must")]})
    with pytest.raises(ValueError, match="spoza"):
        build_result(list(o.values()), ps, cfg(o, {"a": "zzz", "b": o["A"].canonical_id, "kind": "must"}))


def test_unconstrained_material_without_comparisons_is_insufficient(three):
    o = three
    ps = matrix(o, {"AB": 0.9, "AC": None, "BC": None})
    res = build_result(list(o.values()), ps, cfg(o, con(o, "A", "B", "must")))
    st = {a.material_id: a.status.value for a in res.run.assignments}
    assert st[o["C"].canonical_id] == "insufficient_data" and res.excluded == (o["C"].canonical_id,)


def test_deterministic_regardless_of_order(three):
    o, ps = three, matrix(three, BRIDGE)
    c = cfg(o, con(o, "A", "C", "cannot"))
    r1 = build_result(list(o.values()), ps, c).run
    r2 = build_result(list(reversed(list(o.values()))), list(reversed(ps)), c).run
    assert r1.model_dump() == r2.model_dump()


def test_evaluation_hides_constraint_pairs():
    o = {n: obs(n) for n in "ABCD"}
    ps = matrix(o, {"AB": 0.9, "BC": 0.9, "AC": 0.6, "CD": 0.1})
    c = cfg(o, con(o, "A", "C", "cannot"))
    res = build_result(list(o.values()), ps, c)
    pr = lambda x, y: tuple(sorted((o[x].canonical_id, o[y].canonical_id)))
    ref = Reference(labels={"shared_template": {pr("A", "C"): "no", pr("A", "B"): "yes", pr("B", "C"): "yes",
                                                  pr("C", "D"): "no", pr("A", "D"): "no"}})
    ev = evaluate_hidden(res, ref, "shared_template")
    assert ev["reference_pairs"] == 5 and ev["hidden_pairs"] == 1 and ev["evaluated_pairs"] == 4
    m = ev["metrics"]
    assert (m["tp"], m["fn"], m["tn"], m["fp"]) == (1, 1, 2, 0)    # B-C yes nie zostało połączone (cannot A-C)
    assert pr("A", "C") in hidden_pairs(res.constraints, "shared_template", [o[n].canonical_id for n in "ABCD"])


def test_hidden_pairs_cover_transitive_closure():
    cs = parse_constraints([{"a": "a", "b": "b", "kind": "must"}, {"a": "b", "b": "c", "kind": "must"},
                            {"a": "c", "b": "x", "kind": "cannot"}], "shared_template")
    h = hidden_pairs(cs, "shared_template", ["a", "b", "c", "x", "y"])
    assert {("a", "b"), ("a", "c"), ("b", "c"), ("c", "x"), ("a", "x"), ("b", "x")} <= h
    assert ("a", "y") not in h


def test_entry_point_discovered():
    assert "constrained-average-v1" in set(discover(AdapterKind.clusterer))
    assert len(get_adapters()) == 1
