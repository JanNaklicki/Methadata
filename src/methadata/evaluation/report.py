"""Raport oceny par wg §6: TP/FP/FN, precision/recall/F1, pokrycie, statystyki grup, podział, nowy członek,
próbki syntetyczne osobno, przypadki błędne ze wskazaniem dowodów."""
from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from ..contracts import ClusterRun, Observation, PairEvidence
from .labels import AXES, AXIS_RELATION, Pair, Reference
from .metrics import axis_metrics, cluster_stats, decisions_from_run
from .splits import newcomer_top_k, pairs_in_split, split_families

NO_BASIS_TXT = "brak podstaw do wyliczenia"


def _material_tags(observations: Sequence[Observation]) -> dict[str, set]:
    out: dict[str, set] = {}
    for o in observations:
        out.setdefault(o.canonical_id, set()).update(o.tags)
    return out


def build_report(run: ClusterRun, reference: Reference, *, observations: Sequence[Observation] = (),
                 evidence: Sequence[PairEvidence] = (), families: Optional[Mapping[str, str]] = None,
                 split_seed: int = 0, heldout_fraction: float = 0.3, newcomer_queries: Sequence[str] = (),
                 meta: Optional[Mapping[str, Any]] = None) -> dict:
    """Raport jako słownik JSON. Oś jest oceniana na podstawie przynależności do grup (`run`);
    `evidence` służy tylko do wskazania źródeł błędnych predykcji i do testu nowego członka."""
    tags = _material_tags(observations)
    synth = {m for m, t in tags.items() if "synthetic" in t}
    split = split_families(families, seed=split_seed, heldout_fraction=heldout_fraction) if families else None
    all_pairs = set(reference.pairs())
    preds = decisions_from_run(run, all_pairs)
    parts: dict[str, Optional[set]] = {"all": None}
    parts["original"] = {p for p in all_pairs if not (set(p) & synth)} if synth else None
    if synth:
        parts["synthetic"] = {p for p in all_pairs if set(p) & synth}
    if split:
        parts["dev"] = pairs_in_split(all_pairs, split, "dev")
        parts["heldout"] = pairs_in_split(all_pairs, split, "heldout")
    ev_by_pair: dict[Pair, list[PairEvidence]] = {}
    for e in evidence:
        ev_by_pair.setdefault(tuple(e.pair), []).append(e)

    axes: dict[str, Any] = {}
    for axis in AXES:
        ref = reference.axis(axis)
        if not ref:
            continue
        per_part = {name: axis_metrics(ref, preds, ps).to_dict() for name, ps in parts.items()}
        errors = []
        for pair, label in sorted(ref.items()):
            d = preds.get(pair)
            kind = ("FP" if label == "no" and d is True else
                    "FN" if label == "yes" and d is not True else None)
            if kind:
                evs = [e for e in ev_by_pair.get(pair, []) if e.relation.value == AXIS_RELATION[axis]]
                errors.append({"type": kind, "pair": list(pair), "decision": d,
                               "sources": [{"pair_evidence_id": e.pair_evidence_id, "method_id": e.method_id,
                                            "score": e.score, "explanation": e.explanation,
                                            "evidence_refs": {k: [r.model_dump(mode="json") for r in v]
                                                              for k, v in e.evidence_refs.items()}} for e in evs]})
        axes[axis] = {"metrics": per_part,
                      "evaluated_pairs": sorted([list(p), v] for p, v in ref.items() if v in ("yes", "no")),
                      "errors": errors}
    report = {"run_id": run.run_id, "method_id": run.method_id, "method_version": run.method_version,
              "dataset_version": run.dataset_version, "seed": run.seed, "relation": run.relation.value,
              "clusters": cluster_stats(run), "axes": axes,
              "label_issues": {"invalid_labels": len(reference.invalid), "disagreements": len(reference.disagreements)},
              "split": ({"seed": split_seed, "heldout_fraction": heldout_fraction, "assignment": split} if split else None),
              "synthetic_materials": sorted(synth), "meta": dict(meta or {})}
    if newcomer_queries and families:
        rel = run.relation.value
        scores: dict[Pair, float] = {}
        for p, evs in ev_by_pair.items():
            vals = [e.score for e in evs if e.relation.value == rel and e.score is not None]
            if vals:
                scores[p] = max(vals)
        ids = [a.material_id for a in run.assignments]
        key = lambda a, b: (a, b) if a < b else (b, a)
        report["newcomer"] = [newcomer_top_k(q, ids, families, lambda a, b: scores.get(key(a, b)))
                              for q in sorted(newcomer_queries)]
        report["newcomer_note"] = "Scenariusz demo: p15 jako zapytanie przy znanych p2/p7 (znany przykład), nie dowód uogólnienia."
    return report


def _f(x: Optional[float]) -> str:
    return NO_BASIS_TXT if x is None else f"{x:.3f}"


def render_markdown(report: Mapping[str, Any]) -> str:
    L = [f"# Raport oceny par: {report['method_id']} {report['method_version']}",
         f"run `{report['run_id']}` · dane `{report['dataset_version']}` · seed {report['seed']} · relacja {report['relation']}", ""]
    c = report["clusters"]
    L += [f"Grupy: {c['groups']}, największa: {c['largest_group']}, singletony: {c['singletons']}, szum: {c['noise']}, "
          f"niewystarczające dane: {c['insufficient_data']}, materiały: {c['materials']}."]
    t = report.get("meta", {}).get("timings")
    if t:
        L.append("Czas [s]: " + ", ".join(f"{k} {v:.3f}" for k, v in t.items()))
    li = report["label_issues"]
    L += [f"Etykiety: nierozpoznane {li['invalid_labels']}, rozbieżności anotatorów {li['disagreements']} (poza oceną).", ""]
    for axis, a in report["axes"].items():
        L += [f"## Oś: {axis}", "", "| zakres | TP | FP | FN | precision | recall | F1 | pokrycie | ocenione | nieznane | wstrzymane (poz.) |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
        for name, m in a["metrics"].items():
            L.append(f"| {name} | {m['tp']} | {m['fp']} | {m['fn']} | {_f(m['precision'])} | {_f(m['recall'])} | {_f(m['f1'])} "
                     f"| {_f(m['coverage'])} | {m['evaluated']} | {m['unknown']} | {m['abstained_positive']} |")
        if "synthetic" in a["metrics"]:
            L.append("\nWiersz `synthetic` dotyczy par z materiałem syntetycznym; nie łączyć go z wynikiem na oryginałach.")
        if a["errors"]:
            L += ["", "Przypadki błędne (para -> źródła dowodów):"]
            for e in a["errors"]:
                src = "; ".join(f"{s['method_id']} score={s['score']} {s['pair_evidence_id']}" for s in e["sources"]) or "brak PairEvidence"
                L.append(f"- {e['type']} {e['pair'][0]} / {e['pair'][1]}: {src}")
        L.append("")
    if report.get("newcomer"):
        L += ["## Nowy członek znanej rodziny (top-3)", ""]
        for n in report["newcomer"]:
            L.append(f"- {n['query']}: trafienie={n['hit']} top={n['top']} (krewni w indeksie: {n['relatives_in_index']})")
        L += ["", report["newcomer_note"], ""]
    return "\n".join(L)


def compare_reports(a: Mapping[str, Any], b: Mapping[str, Any]) -> dict:
    """Porównanie dwóch przebiegów na tych samych parach: tylko przecięcie ocenionych par; różnice per para."""
    out: dict[str, Any] = {"a": a["run_id"], "b": b["run_id"], "axes": {}}
    for axis in sorted(set(a["axes"]) & set(b["axes"])):
        pa = {tuple(p): v for p, v in a["axes"][axis]["evaluated_pairs"]}
        pb = {tuple(p): v for p, v in b["axes"][axis]["evaluated_pairs"]}
        out["axes"][axis] = {
            "same_pairs": pa == pb, "only_a": sorted(map(list, set(pa) - set(pb))), "only_b": sorted(map(list, set(pb) - set(pa))),
            "metrics_all": {"a": a["axes"][axis]["metrics"]["all"], "b": b["axes"][axis]["metrics"]["all"]},
            "error_pairs": {"only_a": sorted({tuple(e["pair"]) for e in a["axes"][axis]["errors"]} - {tuple(e["pair"]) for e in b["axes"][axis]["errors"]}),
                            "only_b": sorted({tuple(e["pair"]) for e in b["axes"][axis]["errors"]} - {tuple(e["pair"]) for e in a["axes"][axis]["errors"]})}}
        out["axes"][axis]["error_pairs"] = {k: [list(p) for p in v] for k, v in out["axes"][axis]["error_pairs"].items()}
    return out
