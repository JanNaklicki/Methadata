import json
from itertools import combinations
from pathlib import Path

import pytest

from methadata.clustering.incremental import (
    PairBank, attach, build_index, compare_with_full, order_experiment, resolve_config,
)
from methadata.contracts import (
    AdapterKind, Artifact, ArtifactRef, ArtifactRole, AssignmentStatus, Bundle, ClusterRun, InputKind, Observation,
    PairEvidence, Relation, discover, ids,
)

ROOT = Path(__file__).resolve().parents[2]
INC = json.loads((ROOT / "configs/incremental/incremental.json").read_text(encoding="utf-8"))
CFG = {"fusion": {"families": {"s": {"method_id": "syn-sim", "weight": 1.0, "origin": "s", "support_min": 0.5}},
                  "threshold": 0.5, "min_support": 1, "coverage_min": 0.5, "similarity_version": "syn-v1"},
       "dataset_version": "syn@1", "feature_version": "syn-features-v1",
       "sample_size": INC["sample_size"], "min_fraction": INC["min_fraction"]}


def world(names, sims, unobserved=()):
    obs, cid = {}, {}
    for n in sorted(names):
        url = f"https://{n.lower()}.example.invalid/"
        oid = ids.observation_id(f"syn:{n}", "url_only")
        d = ids.sha256_bytes(url.encode())
        art = Artifact(artifact_id=ids.artifact_id(oid, "input_url", d), observation_id=oid,
                       role=ArtifactRole.input_url, sha256=d, url=url)
        o = Observation(observation_id=oid, canonical_id=ids.canonical_id([], url), source_ref=f"syn:{n}",
                        input_kind=InputKind.url_only, artifacts=[art], tags=["synthetic"])
        obs[n], cid[n] = o, o.canonical_id
    raw = []
    for a, b in combinations(sorted(names), 2):
        pair = tuple(sorted((cid[a], cid[b])))
        miss = a in unobserved or b in unobserved
        score = None if miss else sims.get(a + b, sims.get(b + a, 0.0))
        refs = {} if miss else {cid[n]: [ArtifactRef(artifact_id=obs[n].artifacts[0].artifact_id)] for n in (a, b)}
        raw.append(PairEvidence(run_id="syn", pair=pair, method_id="syn-sim", relation=Relation.shared_template,
                                score=score, evidence_refs=refs, explanation="synt"))
    return obs, cid, raw


def start(names, sims, base, **kw):
    obs, cid, raw = world(names, sims, **kw)
    bank = PairBank(raw)
    idx = build_index([obs[n] for n in base], bank.subset([cid[n] for n in base]), CFG)
    return obs, cid, bank, idx


def named(cid, groups):
    inv = {v: k for k, v in cid.items()}
    return sorted(sorted(inv[m] for m in g) for g in groups)


CLUSTER = {"AB": .9, "BC": .9, "AC": .8, "DE": .9}


def test_join_cluster_with_evidence_and_new_version():
    obs, cid, bank, idx = start("ABCDEN", {**CLUSTER, "AN": .9, "BN": .8, "CN": .9}, "ABCDE")
    assert named(cid, idx.groups()) == [["A", "B", "C"], ["D", "E"]] and idx.version == 0
    r = attach(idx, obs["N"], bank)
    assert named(cid, r.index.groups()) == [["A", "B", "C", "N"], ["D", "E"]]
    assert r.index.version == 1 and r.index.parent_id == idx.index_id and r.index.feature_version == "syn-features-v1"
    ev = r.evidence
    assert ev.decision == "joined_cluster" and ev.comparisons <= 3 * 1 + 2 + 2 * 1  # <= (1+s) na klaster
    unit = next(u for u in ev.units if u.qualifies)
    assert unit.size == 3 and len(unit.probes) == 3 and unit.probes[0].role == "representative"
    assert all(p.edge and p.edge.pair_evidence_id == p.pair_evidence_id for p in unit.probes if p.accepted)
    assert {e.pair_evidence_id for e in r.run.edges} >= {p.pair_evidence_id for p in unit.probes if p.accepted}
    assert r.run.config["incremental"]["parent_run_id"] == idx.run.run_id and r.run.dataset_version.endswith("+add:" + cid["N"])
    ClusterRun.model_validate(r.run.model_dump(mode="json"))
    Bundle(observations=list(r.index.observations), pairs=list(r.index.pairs), runs=[r.run]).validate_references()
    # deterministyczny, indeks wejściowy niezmieniony
    assert attach(idx, obs["N"], PairBank(bank.subset(list(cid.values())))).index.index_id == r.index.index_id
    assert idx.version == 0 and "N" not in named(cid, [idx.materials])[0]
    cmp = compare_with_full(r, bank, list(obs.values()))
    assert cmp["new_group_identical"] and cmp["identical_groups"] and cmp["pair_jaccard"] == 1.0
    assert cmp["cost"]["incremental_pair_comparisons"] < cmp["cost"]["full_all_pairs"]


def test_weak_coverage_gives_no_assignment_and_no_match_gives_singleton():
    obs, cid, bank, idx = start("ABCDEN", CLUSTER, "ABCDE", unobserved=("N",))
    r = attach(idx, obs["N"], bank)
    assert r.evidence.decision == "no_assignment" and r.index.groups() == idx.groups()
    a = next(a for a in r.run.assignments if a.material_id == cid["N"])
    assert a.status is AssignmentStatus.insufficient_data and not a.cluster_ids
    obs, cid, bank, idx = start("ABCDEN", CLUSTER, "ABCDE")   # N nic nie przypomina
    r = attach(idx, obs["N"], bank)
    assert r.evidence.decision == "singleton" and r.index.groups() == idx.groups()


def test_pair_formation_with_singleton():
    obs, cid, bank, idx = start("ABCN", {"AB": .9, "AN": .9}, "ABC")
    assert named(cid, idx.groups()) == [["A", "B"]]
    r = attach(idx, obs["N"], bank)
    assert named(cid, r.index.groups()) == [["A", "B", "N"]]
    obs, cid, bank, idx = start("ABCN", {"AB": .9, "CN": .9}, "ABC")
    r = attach(idx, obs["N"], bank)
    assert r.evidence.decision == "formed_pair" and named(cid, r.index.groups()) == [["A", "B"], ["C", "N"]]


def test_failure_case_chain_sample_misses_far_end():
    chain = {"AB": .9, "BC": .9, "CD": .9, "DE": .9, "EN": .9}
    obs, cid, bank, idx = start("ABCDEN", chain, "ABCDE")
    r = attach(idx, obs["N"], bank)
    cmp = compare_with_full(r, bank, list(obs.values()))
    assert r.evidence.decision == "singleton"                     # próbka (reprezentant + 2) nie dosięga końca łańcucha
    assert not cmp["new_group_identical"] and cmp["pair_jaccard"] < 1.0
    assert cmp["new_group_full"] and len(cmp["new_group_full"]) == 6
    wide = start("ABCDEN", chain, "ABCDE")
    full_sample = attach(build_index([wide[0][n] for n in "ABCDE"], wide[2].subset([wide[1][n] for n in "ABCDE"]),
                                     {**CFG, "sample_size": 4}), wide[0]["N"], wide[2])
    assert full_sample.evidence.decision == "joined_cluster" or full_sample.evidence.units[0].coverage == 1.0


def test_bridging_candidate_is_reported_not_merged():
    sims = {"AB": .9, "CD": .9, "AN": .9, "CN": .9}
    obs, cid, bank, idx = start("ABCDN", sims, "ABCD")
    r = attach(idx, obs["N"], bank)
    assert len(r.evidence.also_qualifying) == 1 and r.evidence.decision == "joined_cluster"
    assert len(r.index.groups()) == 2
    assert not compare_with_full(r, bank, list(obs.values()))["identical_groups"]


def test_order_experiment_reports_divergence_and_cost():
    sims = {**CLUSTER, "AN": .9, "BN": .9, "CN": .9, "AM": .9, "BM": .9, "CM": .9, "MN": .9}
    obs, cid, raw = world("ABCDEMN", sims)
    bank = PairBank(raw)
    r = order_experiment(list(obs.values()), bank, CFG, [cid[n] for n in "ABCDE"],
                         [[cid["M"], cid["N"]], [cid["N"], cid["M"]]])
    assert r["order_independent"] and r["orders_identical_to_full"] == 2
    assert r["min_pairwise_jaccard_between_orders"] == 1.0
    assert named(cid, [tuple(g) for g in r["groups_full"]]) == [["A", "B", "C", "M", "N"], ["D", "E"]]


def test_order_dependence_is_measured():
    # M łączy D i N; gdy N dołączony przed M, M widzi dwa singletony i przyrost nie scala ich obu
    sims = {"AB": .9, "AC": .9, "DM": .9, "MN": .9}
    obs, cid, raw = world("ABCDMN", sims)
    r = order_experiment(list(obs.values()), PairBank(raw), CFG, [cid[n] for n in "ABCD"],
                         [[cid["M"], cid["N"]], [cid["N"], cid["M"]]])
    assert not r["order_independent"] and r["min_pairwise_jaccard_between_orders"] < 1.0
    assert r["orders"][0]["identical_to_full"] and not r["orders"][1]["identical_to_full"]
    assert r["orders_identical_to_full"] == 1


def test_config_requires_feature_version_and_unique_material():
    with pytest.raises(ValueError, match="feature_version"):
        resolve_config({k: v for k, v in CFG.items() if k != "feature_version"})
    with pytest.raises(ValueError, match="sample_size"):
        resolve_config({**CFG, "sample_size": -1})
    obs, cid, bank, idx = start("ABN", {"AB": .9}, "ABN")
    with pytest.raises(ValueError, match="już w indeksie"):
        attach(idx, obs["N"], bank)


def test_entry_point_clusterer_matches_attach():
    obs, cid, raw = world("ABCDEN", {**CLUSTER, "AN": .9, "BN": .8, "CN": .9})
    adapter = discover(AdapterKind.clusterer)["incremental-attach-v1"]
    flat = {**CFG["fusion"], "dataset_version": "syn@1", "feature_version": "syn-features-v1",
            "sample_size": 2, "min_fraction": 0.5, "new_material": cid["N"]}
    run = adapter.cluster(list(obs.values()), raw, flat)
    bank = PairBank(raw)
    idx = build_index([obs[n] for n in "ABCDE"], bank.subset([cid[n] for n in "ABCDE"]), CFG)
    assert run == attach(idx, obs["N"], bank, dataset_version="syn@1").run
    assert run.method_id == "incremental-attach"
