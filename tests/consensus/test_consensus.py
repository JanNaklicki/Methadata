import json
from pathlib import Path

import pytest

from methadata.clustering.consensus import ConsensusClusterer, build_result, explain_pair, sweep
from methadata.contracts import (Artifact, ArtifactRole, Assignment, AssignmentStatus, Bundle, ClusterRun, InputKind,
                                 Observation, Relation, discover, ids)
from methadata.contracts.interfaces import AdapterKind

ROOT = Path(__file__).resolve().parents[2]
CFG = {"threshold": 0.6, "similarity_version": "consensus-in-v1", "dataset_version": "synthetic-1", "min_evaluable": 2}


def obs(name):
    url = f"https://{name}.example.invalid/a"
    oid = ids.observation_id(f"synthetic://{name}", "url_only")
    sha = ids.sha256_bytes(name.encode())
    art = Artifact(artifact_id=ids.artifact_id(oid, "input_url", sha), observation_id=oid, role=ArtifactRole.input_url,
                   sha256=sha, url=url)
    return Observation(observation_id=oid, canonical_id=ids.canonical_id([], url), source_ref=f"synthetic://{name}",
                       input_kind=InputKind.url_only, artifacts=[art], tags=["synthetic"])


OBS = {n: obs(n) for n in "abcdef"}
CID = {n: o.canonical_id for n, o in OBS.items()}
NAME = {v: k for k, v in CID.items()}


def run(rid, method, groups, insufficient=(), singles=(), version="sim-v1", **extra):
    asg = []
    for i, g in enumerate(groups):
        asg += [Assignment(material_id=CID[m], cluster_ids=[f"{rid}-{i}"], status=AssignmentStatus.grouped) for m in g]
    asg += [Assignment(material_id=CID[m], status=AssignmentStatus.singleton) for m in singles]
    asg += [Assignment(material_id=CID[m], status=AssignmentStatus.insufficient_data) for m in insufficient]
    return ClusterRun(run_id=rid, dataset_version="synthetic-1", method_id=method, method_version="1",
                      config={"similarity_version": version, **extra}, seed=0, relation=Relation.campaign_candidate,
                      assignments=asg)


def cores(res):
    return {frozenset(NAME[m] for m in c.members) for c in res.cores}


def build(runs, **cfg):
    return build_result(list(OBS.values()), runs, (), {**CFG, **cfg})


def test_agreement_and_cores():
    runs = [run("r1", "m1", [["a", "b", "c"]], singles="def"), run("r2", "m2", [["a", "b", "c"]], singles="def"),
            run("r3", "m3", [["a", "b"], ["c", "d"]], singles="ef")]
    res = build(runs, weighting="run", threshold=0.6)
    assert cores(res) == {frozenset("abc")}
    p = res.pairs[tuple(sorted((CID["a"], CID["b"])))]
    assert (p.together, p.evaluable_runs, p.agreement) == (3, 3, 1.0)
    q = res.pairs[tuple(sorted((CID["a"], CID["c"])))]
    assert (q.together, q.evaluable_runs) == (2, 3) and q.agreement == pytest.approx(2 / 3)
    assert res.run.config["input_runs"][0]["similarity_version"] == "sim-v1"
    Bundle(observations=list(OBS.values()), pairs=list(res.pair_evidence), runs=[res.run])  # waliduje odwołania


def test_denominator_counts_only_evaluable_pairs():
    runs = [run("r1", "m1", [["a", "b"]], singles="cdef"), run("r2", "m2", [["a", "b"]], singles="cdef"),
            run("r3", "m3", [], insufficient="ab", singles="cdef"), run("r4", "m4", [], insufficient="ab", singles="cdef")]
    res = build(runs, weighting="run", threshold=0.9)
    p = res.pairs[tuple(sorted((CID["a"], CID["b"])))]
    assert p.evaluable_runs == 2 and p.agreement == 1.0          # r3, r4 nie wchodzą do mianownika
    assert cores(res) == {frozenset("ab")}
    assert res.material_evaluable_runs[CID["a"]] == 2


def test_min_evaluable_blocks_edge():
    runs = [run("r1", "m1", [["a", "b"]], singles="cdef"), run("r2", "m2", [], insufficient="ab", singles="cdef")]
    res = build(runs, weighting="run", min_evaluable=2)
    assert cores(res) == set()
    assert {a.status for a in res.run.assignments if a.material_id == CID["a"]} == {AssignmentStatus.insufficient_data}
    assert cores(build(runs, weighting="run", min_evaluable=1)) == {frozenset("ab")}


def test_family_removal_and_dominance():
    # trzy podobne metody (rodzina "graph") głosują razem; jedna metoda z innej rodziny jest przeciw
    runs = [run(f"g{i}", f"graph{i}", [["a", "b"]], singles="cdef", method_family="graph") for i in range(3)]
    runs.append(run("t1", "text", [], singles="abcdef", method_family="text"))
    by_run = build(runs, weighting="run", threshold=0.7)
    by_fam = build(runs, weighting="family", threshold=0.7)
    assert cores(by_run) == {frozenset("ab")}         # 3 podobne głosy przeważają
    assert cores(by_fam) == set()                     # rodzina liczy się jako jeden głos (0.5 < 0.7)
    abl = {x.family: x for x in by_run.ablations}
    assert abl["text"].core_pair_retention == 1.0 and not abl["text"].dominant
    assert abl["graph"].core_pair_retention == 0.0 and abl["graph"].dominant
    assert any("graph" in w for w in by_run.warnings) and not by_run.dominance_ok


def test_single_vote_decisive():
    runs = [run("r1", "m1", [["a", "b"]], singles="cdef"), run("r2", "m2", [["a", "b"]], singles="cdef"),
            run("r3", "m3", [], singles="abcdef")]
    res = build(runs, weighting="run", threshold=0.6, min_evaluable=1)
    assert cores(res) == {frozenset("ab")}
    assert res.single_vote["r1"]["decisive_edges"] == 1 and res.single_vote["r3"]["decisive_edges"] == 0
    p = res.pairs[tuple(sorted((CID["a"], CID["b"])))]
    assert set(p.decisive_runs) == {"r1", "r2"}


def test_min_families():
    runs = [run("r1", "m1", [["a", "b"]], singles="cdef", method_family="x"),
            run("r2", "m2", [["a", "b"]], singles="cdef", method_family="x")]
    assert cores(build(runs, weighting="run", min_families=1)) == {frozenset("ab")}
    assert cores(build(runs, weighting="run", min_families=2)) == set()


def test_chain_guard_drops_bridge_member():
    # a-b-c-d tworzą łańcuch: tylko sąsiednie pary są razem
    runs = [run("r1", "m1", [["a", "b"], ["c", "d"]]), run("r2", "m2", [["b", "c"]], singles="adef"),
            run("r3", "m3", [["a", "b"], ["c", "d"]]), run("r4", "m4", [["b", "c"]], singles="adef")]
    res = build(runs, weighting="run", threshold=0.5, min_evaluable=2)
    assert all(len(c) == 2 for c in cores(res))


def test_requires_versions_and_dataset():
    r = run("r1", "m1", [["a", "b"]], singles="cdef")
    bad = r.model_copy(update={"config": {}})
    with pytest.raises(ValueError, match="wersji"):
        build([bad])
    with pytest.raises(ValueError, match="dataset_version"):
        build([r.model_copy(update={"dataset_version": "inny"})])
    with pytest.raises(ValueError, match="similarity_version"):
        build_result(list(OBS.values()), [r], (), {"threshold": 0.5, "dataset_version": "synthetic-1"})
    with pytest.raises(ValueError, match="powtórzony"):
        build([r, r])


def test_deterministic_and_order_independent():
    runs = [run("r1", "m1", [["a", "b", "c"]], singles="def"), run("r2", "m2", [["a", "b"]], singles="cdef")]
    r1 = build(runs, threshold=0.5)
    r2 = build_result(list(reversed(list(OBS.values()))), list(reversed(runs)), (), {**CFG, "threshold": 0.5})
    assert r1.run == r2.run


def test_edges_have_evidence_and_explain():
    runs = [run("r1", "m1", [["a", "b"]], singles="cdef"), run("r2", "m2", [["a", "b"]], singles="cdef")]
    res = build(runs, weighting="run")
    by_id = {p.pair_evidence_id: p for p in res.pair_evidence}
    assert all(e.pair_evidence_id in by_id for e in res.run.edges)
    ex = explain_pair(res, CID["b"], CID["a"])
    assert ex["edge"] and ex["evaluable_runs"] == 2 and len(ex["votes"]) == 2
    assert explain_pair(res, CID["a"], CID["c"])["together"] == 0


def test_sweep_and_adapter_and_discover():
    runs = [run("r1", "m1", [["a", "b", "c"]], singles="def"), run("r2", "m2", [["a", "b"]], singles="cdef")]
    rows = sweep(list(OBS.values()), runs, CFG, [0.4, 1.0])
    assert rows[0]["grouped"] >= rows[1]["grouped"]
    out = ConsensusClusterer().cluster(list(OBS.values()), [], CFG, optional={"runs": runs})
    assert out.method_id == "consensus-cores"
    assert "consensus-cores-v1" in discover(AdapterKind.clusterer, strict=False)
    with pytest.raises(ValueError):
        ConsensusClusterer().cluster(list(OBS.values()), [], CFG)


def test_config_file_valid():
    cfg = json.loads((ROOT / "configs" / "consensus" / "consensus.json").read_text(encoding="utf-8"))
    cfg.pop("name")
    from methadata.clustering.consensus import resolve_config
    resolve_config({**cfg, "dataset_version": "x"})


# ------------------------------------------------------------ rzeczywisty korpus (pomijany bez BACKUP)

from methadata.devtools.realdata import BACKUP  # noqa: E402


@pytest.mark.skipif(not BACKUP.is_dir(), reason="brak katalogu BACKUP")
def test_real_corpus(tmp_path):
    from methadata.clustering.consensus.run import run_consensus
    from methadata.ingest.__main__ import main as ingest_main
    store = tmp_path / "store"
    assert ingest_main(["import", "--store", str(store), *sorted(str(p) for p in BACKUP.glob("p*") if p.is_dir())]) == 0
    load = lambda *p: json.loads(ROOT.joinpath(*p).read_text(encoding="utf-8"))  # noqa: E731
    base, fcfg, ccfg = load("configs", "baseline", "baseline.json"), load("configs", "fusion", "fusion.json"), \
        load("configs", "consensus", "consensus.json")
    fcfg.pop("name")
    rep = run_consensus(store, base, fcfg, ccfg)["report"]
    assert sorted(sorted(c["members"]) for c in rep["cores"]) == [["p11", "p8"], ["p15", "p2", "p7"]]
    assert all(c["min_evaluable_runs"] == len(rep["input_runs"]) for c in rep["cores"])
    byp = {frozenset((p["a"], p["b"])): p for p in rep["pairs_top"]}
    near = byp[frozenset(("p3", "p4"))]          # przypadek zawodności: podobny tekst, brak wspólnych grafik
    assert not near["edge"] and 0.5 < near["agreement"] < ccfg["threshold"]
    assert not rep["warnings"]
