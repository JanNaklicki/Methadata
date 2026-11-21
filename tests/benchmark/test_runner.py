import json
import random
import shutil
from pathlib import Path

from methadata.contracts import (AdapterKind, AdapterSpec, Assignment, AssignmentStatus, Bundle, ClusterRun, Feature,
                                 PairEvidence, ArtifactRef)
from methadata.evaluation import Reference, build_report
from methadata.runner import evaluate_run, load_run, run_experiment, save_run
from methadata.runner.cli import main

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "contract" / "ui_demo.json"
GROUP = {"ad-001": "x", "ad-002": "x", "ad-003": "x", "ad-004": "y"}


class Ex:
    spec = AdapterSpec(id="toy-ex", kind=AdapterKind.extractor, version="1")

    def extract(self, o, *, reader, optional=None):
        return [Feature(feature_id="ftr-" + o.observation_id[4:], observation_id=o.observation_id, family="toy", kind="grp",
                        value_type="string", value=GROUP[o.source_ref.rsplit("/", 1)[1]], extractor_version="toy-1")], []


class Cmp:
    spec = AdapterSpec(id="toy-cmp", kind=AdapterKind.comparator, version="2")

    def compare(self, a, b, features, *, run_id, optional=None):
        fa, fb = features[a.observation_id][0].value, features[b.observation_id][0].value
        ra, rb = (ArtifactRef(artifact_id=x.artifacts[0].artifact_id) for x in (a, b))
        return [PairEvidence(run_id=run_id, pair=tuple(sorted((a.canonical_id, b.canonical_id))), method_id="toy-cmp",
                             relation="shared_template", score=float(fa == fb), explanation="ta sama grupa",
                             evidence_refs={a.canonical_id: [ra], b.canonical_id: [rb]})]


class Clu:
    spec = AdapterSpec(id="toy-clu", kind=AdapterKind.clusterer, version="3")

    def cluster(self, observations, pair_evidence, config, *, seed=0, optional=None):
        ids = sorted(o.canonical_id for o in observations)
        parent = {i: i for i in ids}
        def find(x):
            while parent[x] != x:
                x = parent[x]
            return x
        for p in pair_evidence:
            if p.score is not None and p.score >= config.get("threshold", 0.5):
                parent[find(p.pair[1])] = find(p.pair[0])
        comp = {}
        for i in ids:
            comp.setdefault(find(i), []).append(i)
        asg = []
        for members in comp.values():
            for m in members:
                asg.append(Assignment(material_id=m, cluster_ids=["c-" + min(members)[-6:]], status="grouped") if len(members) > 1
                           else Assignment(material_id=m, status="singleton"))
        return ClusterRun(run_id="x", dataset_version="x", method_id="toy-clu", method_version="3", seed=seed,
                          relation="shared_template", assignments=asg)


ADAPTERS = {a.spec.id: a for a in (Ex(), Cmp(), Clu())}
CFG = {"dataset_version": "demo-1", "seed": 7, "relation": "shared_template", "clusterer": "toy-clu",
       "cluster_config": {"threshold": 0.5}, "split": {"seed": 1, "heldout_fraction": 0.5}}


def _obs():
    return Bundle.model_validate_json(FIX.read_text()).observations


def _canon(res):
    r = dict(res["record"]); r.pop("timings")
    return json.dumps(r, sort_keys=True), res["bundle"].model_dump_json()


def test_order_independent():
    obs = _obs()
    base = _canon(run_experiment(CFG, obs, ADAPTERS))
    for s in range(4):
        sh = obs[:]; random.Random(s).shuffle(sh)
        assert _canon(run_experiment(CFG, sh, ADAPTERS)) == base


def test_record_and_evaluation(tmp_path):
    res = run_experiment(CFG, _obs(), ADAPTERS)
    rec = res["record"]
    assert rec["seed"] == 7 and rec["dataset_version"] == "demo-1" and rec["feature_versions"] == ["toy-1"]
    assert rec["adapters"]["toy-cmp"]["version"] == "2" and rec["split"] == CFG["split"] and "total" in rec["timings"]
    assert rec["counts"]["pairs_total"] == 6
    save_run(res, tmp_path)
    rec2, b2 = load_run(tmp_path)
    ids = {o.source_ref[-1]: o.canonical_id for o in b2.observations}
    pair = lambda a, b: tuple(sorted((ids[a], ids[b])))
    ref = Reference(labels={"shared_template": {pair("1", "2"): "yes", pair("1", "3"): "yes", pair("1", "4"): "yes",
                                                 pair("2", "4"): "no", pair("3", "4"): "unknown"}})
    rep = evaluate_run(rec2, b2, ref, manifest={"families": {ids["1"]: "x", ids["2"]: "x", ids["3"]: "x", ids["4"]: "y"}})
    m = rep["axes"]["shared_template"]["metrics"]["all"]
    assert (m["tp"], m["fp"], m["fn"], m["unknown"]) == (2, 0, 1, 1)
    assert rep["axes"]["shared_template"]["errors"][0]["type"] == "FN"
    assert rep["axes"]["shared_template"]["errors"][0]["sources"][0]["method_id"] == "toy-cmp"
    assert "synthetic" in rep["axes"]["shared_template"]["metrics"]  # próbki syntetyczne osobno
    assert rep["split"] is not None


def test_cli_labels_and_compare(tmp_path):
    h = "pair_a,pair_b,axis,label,rationale\n"
    (tmp_path / "a.csv").write_text(h + "A,B,shared_template,tak,\n")
    (tmp_path / "b.csv").write_text(h + "A,B,shared_template,tak,\n")
    assert main(["import-labels", "--a", str(tmp_path / "a.csv"), "--b", str(tmp_path / "b.csv"), "--out", str(tmp_path / "ref.json")]) == 0
    res = run_experiment(CFG, _obs(), ADAPTERS)
    save_run(res, tmp_path / "run")
    ids = sorted(o.canonical_id for o in res["bundle"].observations)
    (tmp_path / "ref.json").write_text(json.dumps({"labels": {"shared_template": [[ids[0], ids[1], "yes"]]}}))
    for n in ("r1", "r2"):
        assert main(["evaluate", "--run", str(tmp_path / "run"), "--reference", str(tmp_path / "ref.json"), "--out", str(tmp_path / n)]) == 0
    assert main(["compare", "--a", str(tmp_path / "r1"), "--b", str(tmp_path / "r2"), "--out", str(tmp_path / "c.json")]) == 0
    assert json.loads((tmp_path / "c.json").read_text())["axes"]["shared_template"]["same_pairs"] is True
