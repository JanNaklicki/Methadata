"""Ręcznie policzony zbiór: materiały A..E, grupa {A,B,E}, C singleton, D insufficient_data.
Referencja (oś szablon): AB tak, AC tak, BC tak, AD tak, AE nie, BE nie, CE nie, CD nie, DE nie wiadomo.
Decyzje: AB T->TP; AC,BC F->FN; AD None->FN(wstrzymanie); AE,BE T->FP; CE F->TN; CD None (neg.)."""
import pytest

from methadata.contracts import Assignment, AssignmentStatus as S, ClusterRun
from methadata.evaluation import (LabelError, axis_metrics, build_report, decisions_from_run, import_annotations,
                                  norm_label, render_markdown, split_families)
from methadata.evaluation.splits import newcomer_top_k

REF = {("A", "B"): "yes", ("A", "C"): "yes", ("B", "C"): "yes", ("A", "D"): "yes", ("A", "E"): "no",
       ("B", "E"): "no", ("C", "E"): "no", ("C", "D"): "no", ("D", "E"): "unknown", ("A", "Z"): "yes"}


def _run():
    asg = [Assignment(material_id=m, cluster_ids=["c1"], status=S.grouped) for m in "ABE"]
    asg += [Assignment(material_id="C", status=S.singleton), Assignment(material_id="D", status=S.insufficient_data)]
    return ClusterRun(run_id="r1", dataset_version="d1", method_id="m", method_version="1", seed=0,
                      relation="shared_template", assignments=asg)


def test_hand_counted():
    ref = dict(REF); del ref[("A", "Z")]
    m = axis_metrics(ref, decisions_from_run(_run(), set(ref)))
    assert (m.tp, m.fp, m.fn, m.tn) == (1, 2, 3, 1)
    assert (m.abstained_positive, m.abstained_negative, m.unknown, m.evaluated) == (1, 1, 1, 8)
    assert m.precision == pytest.approx(1 / 3) and m.recall == pytest.approx(1 / 4)
    assert m.f1 == pytest.approx(2 / 7) and m.coverage == pytest.approx(6 / 8)


def test_missing_material_is_abstention():
    m = axis_metrics(REF, decisions_from_run(_run(), set(REF)))   # A-Z: Z poza przebiegiem
    assert m.abstained_positive == 2 and m.fn == 4 and m.evaluated == 9


def test_zero_denominators():
    ref = {("A", "B"): "no", ("A", "C"): "no"}
    m = axis_metrics(ref, {("A", "B"): False, ("A", "C"): False})
    assert m.recall is None and m.precision is None and m.f1 is None and m.coverage == 1.0
    m = axis_metrics({("A", "B"): "yes"}, {})   # wszystko wstrzymane
    assert m.precision is None and m.recall == 0.0 and m.f1 is None and m.coverage == 0.0
    m = axis_metrics({}, {})
    assert m.coverage is None and m.evaluated == 0
    m = axis_metrics({("A", "B"): "unknown"}, {("A", "B"): True})
    assert m.unknown == 1 and m.evaluated == 0 and m.precision is None


def test_f1_zero_when_defined():
    m = axis_metrics({("A", "B"): "yes", ("A", "C"): "no"}, {("A", "B"): False, ("A", "C"): True})
    assert m.precision == 0.0 and m.recall == 0.0 and m.f1 == 0.0


def test_unknown_label(tmp_path):
    assert norm_label(" Nie  wiadomo ") == "unknown" and norm_label("TAK") == "yes"
    with pytest.raises(LabelError):
        norm_label("chyba")
    h = "pair_a,pair_b,axis,label,rationale\n"
    (tmp_path / "a.csv").write_text(h + "A,B,shared_template,tak,x\nA,C,shared_template,nie,\nB,C,shared_template,chyba,\n")
    (tmp_path / "b.csv").write_text(h + "B,A,shared_template,tak,y\nA,C,shared_template,nie wiadomo,\nB,C,shared_template,nie,\n")
    with pytest.raises(LabelError):
        import_annotations(tmp_path / "a.csv", tmp_path / "b.csv")
    ref = import_annotations(tmp_path / "a.csv", tmp_path / "b.csv", strict=False)
    assert ref.axis("shared_template") == {("A", "B"): "yes", ("A", "C"): "disputed", ("B", "C"): "disputed"}
    assert len(ref.invalid) == 1 and len(ref.disagreements) == 2
    (tmp_path / "adj.csv").write_text(h + "A,C,shared_template,nie,po przeglądzie\n")
    ref = import_annotations(tmp_path / "a.csv", tmp_path / "b.csv", tmp_path / "adj.csv", strict=False)
    assert ref.axis("shared_template")[("A", "C")] == "no"


def test_split_keeps_families_and_order_independent():
    fam = {f"m{i}": f"f{i // 3}" for i in range(12)}
    s1 = split_families(fam, seed=1)
    s2 = split_families(dict(reversed(list(fam.items()))), seed=1)
    assert s1 == s2
    for f in set(fam.values()):
        assert len({s1[m] for m, ff in fam.items() if ff == f}) == 1
    assert "heldout" in s1.values() and "dev" in s1.values()


def test_newcomer_top3():
    fam = {"q": "f", "r": "f", "x": "g", "y": "g", "z": "g", "w": "g"}
    sc = {("q", "r"): 0.9, ("q", "x"): 0.5, ("q", "y"): 0.4, ("q", "z"): 0.3, ("q", "w"): 0.2}
    f = lambda a, b: sc.get((a, b))
    assert newcomer_top_k("q", fam, fam, f)["hit"] is True
    sc[("q", "r")] = 0.1
    assert newcomer_top_k("q", fam, fam, f)["hit"] is False
    assert newcomer_top_k("x", ["q", "r"], {"x": "g", "q": "f", "r": "f"}, f)["hit"] is None


def test_report_has_synthetic_row_and_text():
    ref = type("R", (), {})
    from methadata.evaluation import Reference
    r = Reference(labels={"shared_template": dict(REF)})
    rep = build_report(_run(), r)
    assert rep["axes"]["shared_template"]["metrics"]["all"]["tp"] == 1
    txt = render_markdown(rep)
    assert "Oś: shared_template" in txt
