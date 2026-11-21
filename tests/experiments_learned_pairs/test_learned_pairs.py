import json
import random

import pytest

from methadata.experiments.learned_pairs import (LearnedPairComparator, LearnedPairsError, Logit, check_frozen,
                                                 freeze_split, run, write_report)
from methadata.experiments.learned_pairs import core

M = ["a", "b"]


def synth(n_fam=12, seed=1):
    rnd = random.Random(seed)
    fams = {f"m{f}_{i}": f"F{f}" for f in range(n_fam) for i in range(3)}
    mats = sorted(fams)
    scores, labels = {}, {}
    for i, x in enumerate(mats):
        for y in mats[i + 1:]:
            same = fams[x] == fams[y]
            if not same and rnd.random() > 0.15:
                continue
            p = (x, y)
            sa = rnd.uniform(0.6, 1) if same else rnd.uniform(0, 0.4)
            scores[p] = {"a": sa, "b": None if rnd.random() < 0.3 else rnd.random()}
            labels[p] = "yes" if same else "no"
    return scores, labels, fams


def test_split_frozen_and_no_leak():
    scores, labels, fams = synth()
    fr = freeze_split(fams)
    sp = check_frozen(fr)
    for f in set(fams.values()):
        assert len({sp[m] for m in fams if fams[m] == f}) == 1
    fr["split"][next(iter(fr["split"]))] = "x"
    with pytest.raises(LearnedPairsError):
        check_frozen(fr)


def test_run_learns_and_explains():
    scores, labels, fams = synth()
    rep = run(scores, labels, fams, M, source="synthetic")
    assert rep["status"] == "ok" and rep["source"] == "synthetic"
    assert rep["heldout"]["auc"] > 0.9
    assert rep["learning_curve"] and rep["manual_labels_hint"]["advice"]
    assert rep["examples"][0]["why"][0]["feature"].startswith(("sim:", "__missing__:"))
    q = rep["active_queue"]
    assert all(q[i]["uncertainty"] >= q[i + 1]["uncertainty"] for i in range(len(q) - 1))
    labeled = {tuple(p) for p in labels}
    assert not any(tuple(r["pair"]) in labeled for r in q)


def test_insufficient_data_names_gap():
    scores, labels, fams = synth()
    labels = {p: "yes" for p in labels}
    rep = run(scores, labels, fams, M)
    assert rep["status"] == "insufficient_data" and rep["missing"]
    rep2 = run(scores, {p: "unknown" for p in labels}, fams, M)
    assert rep2["status"] == "insufficient_data"


def test_logit_requires_both_classes():
    with pytest.raises(ValueError):
        Logit(["x"]).fit([[1.0], [0.5]], [1, 1])


def test_missing_indicator_used():
    from methadata.experiments.learned_pairs import featurize
    assert featurize({"a": 0.5}, M) == [0.5, 0.0, 0.0, 1.0]


def test_write_and_cli(tmp_path):
    scores, labels, fams = synth()
    d = {"methods": M, "source": "synthetic", "families": fams,
         "scores": [[a, b, s] for (a, b), s in scores.items()], "labels": [[a, b, v] for (a, b), v in labels.items()]}
    inp = tmp_path / "in.json"
    inp.write_text(json.dumps(d))
    from methadata.experiments.learned_pairs.__main__ import main
    assert main(["--input", str(inp), "--out", str(tmp_path / "o")]) == 0
    assert json.loads((tmp_path / "o" / "learned_pairs.json").read_text())["status"] == "ok"
    assert write_report({"x": 1}, tmp_path / "p").exists()


def _obs(n):
    from types import SimpleNamespace
    return SimpleNamespace(observation_id="o" + n, canonical_id="c" + n)


def test_comparator_without_model_is_none():
    o = _obs
    ev = LearnedPairComparator().compare(o("1"), o("2"), {}, run_id="r")
    assert ev[0].score is None


def test_comparator_with_base_returns_evidence():
    from methadata.contracts import ArtifactRef, PairEvidence, Relation

    class Base:
        def compare(self, a, b, features, *, run_id, optional=None):
            return [PairEvidence(run_id=run_id, pair=("c1", "c2"), method_id="a", relation=Relation.shared_template,
                                 score=0.9, evidence_refs={"c1": [ArtifactRef(artifact_id="x1")], "c2": [ArtifactRef(artifact_id="x2")]},
                                 explanation="e")]
    model = Logit(["sim:a", "sim:b", "__missing__:a", "__missing__:b"], weights=[4, 0, 0, 0], bias=-2)
    o = _obs
    ev = LearnedPairComparator(model, [Base()], M).compare(o("1"), o("2"), {}, run_id="r")[0]
    assert ev.score > 0.8 and "sim:a" in ev.explanation


def test_discover_finds_adapter():
    from methadata.contracts import discover
    assert "learned-pairs-logit-v1" in discover()
