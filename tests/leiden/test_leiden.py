import itertools

import pytest

pytest.importorskip("leidenalg")

from methadata.clustering.leiden import (build_result, explain_pair, get_adapters, partition_agreement,  # noqa: E402
                                         resolve_config, sweep)
from methadata.contracts import (Artifact, ArtifactRef, ArtifactRole, ClusterRun, InputKind, Observation,  # noqa: E402
                                 PairEvidence, Relation, discover, ids)
from methadata.contracts.interfaces import AdapterKind  # noqa: E402

CFG = {"similarity_version": "asset-jaccard-v1", "dataset_version": "synthetic-1", "repeats": 6}
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
    return [pair(o[x], o[y], table.get(x + y, default)) for x, y in itertools.combinations(sorted(o), 2)]


def groups(run):
    g = {}
    for a in run.assignments:
        for c in a.cluster_ids:
            g.setdefault(c, set()).add(a.material_id)
    return {frozenset(v) for v in g.values()}


def named(o, *names):
    return frozenset(o[n].canonical_id for n in names)


TWO = {"AB": .9, "AC": .8, "BC": .9, "DE": .9, "DF": .8, "EF": .9, "CD": .4}  # dwie trójki + słaby most C-D


@pytest.fixture
def six():
    return {n: obs(n) for n in "ABCDEF"}


def test_two_cliques_split_and_run_valid(six):
    res = build_result(list(six.values()), matrix(six, TWO), {**CFG, "resolution": 0.5}, seed=1)
    assert groups(res.run) == {named(six, *"ABC"), named(six, *"DEF")}
    ClusterRun.model_validate(res.run.model_dump())
    assert res.run.config["resolution"] == 0.5 and res.run.seed == 1 and res.run.config["seeds"][0] == 1
    assert res.run.config["similarity_version"] == "asset-jaccard-v1"
    for c in res.clusters:  # każda krawędź grupy ma dowód
        assert all(e.pair_evidence_id for e in c.edges) and c.weakest is not None
    assert res.agreement["mean_ari"] == pytest.approx(1.0) and res.agreement["identical_fraction"] == 1.0


def test_config_requires_versions(six):
    with pytest.raises(ValueError, match="similarity_version"):
        resolve_config({"dataset_version": "x"})
    with pytest.raises(ValueError, match="dataset_version"):
        resolve_config({"similarity_version": "x"})
    with pytest.raises(ValueError):
        resolve_config({**CFG, "quality": "foo"})


def test_resolution_controls_group_size(six):
    rows = sweep(list(six.values()), matrix(six, TWO), CFG, [0.0, 0.5, 0.95], seeds=[0, 1, 2, 3])
    by = {r["resolution"]: r for r in rows}
    assert by[0.0]["largest"] == 6                      # rozdzielczość 0: wszystko jedna grupa (graf spójny)
    assert by[0.5]["largest"] == 3 and by[0.5]["n_groups"] == 2
    assert by[0.95]["largest"] == 1                     # powyżej każdej wagi: same pojedynki
    assert by[0.95]["n_groups"] == 0 and by[0.95]["n_singletons"] == 6
    assert all(r["mean_ari"] == pytest.approx(1.0) for r in rows)  # dane jednoznaczne: pełna zgodność


def test_modularity_quality_also_splits(six):
    res = build_result(list(six.values()), matrix(six, TWO), {**CFG, "quality": "modularity", "resolution": 1.0})
    assert groups(res.run) == {named(six, *"ABC"), named(six, *"DEF")}


def test_deterministic_for_same_seed_and_order(six):
    ps = matrix(six, TWO)
    a = build_result(list(six.values()), ps, CFG, seed=3)
    b = build_result(list(reversed(list(six.values()))), list(reversed(ps)), CFG, seed=3)
    assert a.run == b.run and a.partitions == b.partitions


def test_popular_trace_downweight(six):
    # H łączy się ze wszystkimi (popularny ślad); bez obniżenia wagi skleja obie grupy
    o = {**six, "H": obs("H")}
    table = {**TWO, **{n + "H": 0.7 for n in "ABCDEF"}}
    ps = matrix(o, table)
    plain = build_result(list(o.values()), ps, {**CFG, "resolution": 0.2, "popularity_alpha": 0.0})
    down = build_result(list(o.values()), ps, {**CFG, "resolution": 0.2, "popular_degree": 3, "popularity_alpha": 1.0})
    assert named(o, *"ABCDEFH") in groups(plain.run)
    assert named(o, *"ABCDEFH") not in groups(down.run)
    hub = [e for e in down.graph_edges if o["H"].canonical_id in (e.source, e.target)]
    assert all(e.adjusted < e.score and e.popularity_factor < 1 for e in hub)
    assert all(e.popularity_factor == 1.0 for e in plain.graph_edges)


def test_boundary_pair_evidence_and_alternative(six):
    # graniczny materiał C: silnie do A,B, słabsze do D; para C-D na granicy
    res = build_result(list(six.values()), matrix(six, TWO), {**CFG, "resolution": 0.5})
    ex = explain_pair(res, six["C"].canonical_id, six["D"].canonical_id)
    assert ex["direct_edge"].score == 0.4 and ex["direct_edge"].pair_evidence_id
    assert not ex["same_cluster"] and ex["co_membership_frequency"] == 0.0
    c = ex["assignment"][six["C"].canonical_id]
    assert c["to_own"] == pytest.approx(1.7)
    alt = dict(c["alternatives"])
    d_cluster = ex["assignment"][six["D"].canonical_id]["cluster_id"]
    assert alt[d_cluster] == pytest.approx(0.4)          # alternatywne przypisanie z wagą dowodu
    assert c["strongest_edges"][0].score in (0.9,)
    # przy niskiej rozdzielczości ta sama para jest razem
    low = build_result(list(six.values()), matrix(six, TWO), {**CFG, "resolution": 0.0})
    assert explain_pair(low, six["C"].canonical_id, six["D"].canonical_id)["same_cluster"]


def test_agreement_reports_instability_on_ambiguous_graph():
    # ścieżka o równych wagach: wiele równoważnych podziałów -> zgodność < 1 dla jakiegoś seeda lub pełna; raport musi istnieć
    o = {n: obs(n) for n in "ABCDEFGH"}
    table = {a + b: 0.5 for a, b in zip("ABCDEFG", "BCDEFGH")}
    res = build_result(list(o.values()), matrix(o, table), {**CFG, "quality": "modularity", "resolution": 1.0,
                                                          "repeats": 20})
    assert -0.5 <= res.agreement["min_ari"] <= res.agreement["mean_ari"] <= 1.0
    assert 0.0 < res.agreement["identical_fraction"] <= 1.0
    assert all(0.0 <= v <= 1.0 for v in res.co_membership.values())


def test_partition_agreement_ari():
    p = [("a", "b"), ("c", "d")]
    assert partition_agreement(p, [("c", "d"), ("a", "b")]) == pytest.approx(1.0)
    assert partition_agreement(p, [("a", "c"), ("b", "d")]) < 0
    with pytest.raises(ValueError):
        partition_agreement(p, [("a", "b")])


def test_missing_comparisons_are_not_zero(six):
    table = {**TWO, **{n + "F": None for n in "ABCDE"}}
    res = build_result(list(six.values()), matrix(six, table), {**CFG, "resolution": 0.5})
    st = {a.material_id: a.status.value for a in res.run.assignments}
    assert st[six["F"].canonical_id] == "insufficient_data" and res.excluded == (six["F"].canonical_id,)
    assert groups(res.run) == {named(six, *"ABC"), named(six, "D", "E")}


def test_singleton_when_no_edge(six):
    res = build_result(list(six.values()), matrix(six, {"AB": .9}), {**CFG, "resolution": 0.5})
    st = {a.material_id: a.status.value for a in res.run.assignments}
    assert st[six["C"].canonical_id] == "singleton" and groups(res.run) == {named(six, "A", "B")}


def test_discover_returns_leiden_adapter_and_runs(six):
    found = [a for a in discover().values() if a.spec.id == "leiden-v1"]
    assert len(found) == 1 and found[0].spec.kind == AdapterKind.clusterer
    run = found[0].cluster(list(six.values()), matrix(six, TWO), {**CFG, "resolution": 0.5}, seed=0)
    assert len(groups(run)) == 2
    assert [a.spec.id for a in get_adapters()] == ["leiden-v1"]
