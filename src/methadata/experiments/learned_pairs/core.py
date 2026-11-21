"""E17: uczenie podobieństwa na ocenionych parach, aktywne uczenie, ocena na odłożonych rodzinach."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from methadata.evaluation import norm_pair, pairs_in_split, split_families

from .model import Logit, auc, feature_names, featurize

Pair = tuple[str, str]
MIN_PER_CLASS_DEV = 3
MIN_PER_CLASS_HELD = 2


class LearnedPairsError(ValueError):
    pass


def freeze_split(families: Mapping[str, str], *, seed: int = 0, heldout_fraction: float = 0.3) -> dict:
    """Zamrożony podział rodzin (przed uczeniem): odcisk pozwala wykryć późniejszą zmianę."""
    split = split_families(families, seed=seed, heldout_fraction=heldout_fraction)
    body = {"seed": seed, "heldout_fraction": heldout_fraction, "split": split}
    body["sha256"] = hashlib.sha256(json.dumps(split, sort_keys=True).encode()).hexdigest()
    return body


def check_frozen(frozen: Mapping) -> dict[str, str]:
    sha = hashlib.sha256(json.dumps(frozen["split"], sort_keys=True).encode()).hexdigest()
    if sha != frozen.get("sha256"):
        raise LearnedPairsError("podział zmieniony po zamrożeniu")
    return dict(frozen["split"])


def build_dataset(scores: Mapping[Pair, Mapping[str, Optional[float]]], labels: Mapping[Pair, str],
                  methods: Sequence[str]) -> dict[Pair, tuple[list[float], int]]:
    """Tylko pary ocenione tak/nie (nie wiadomo i spór nie są etykietą)."""
    out = {}
    for pair, lab in labels.items():
        if lab not in ("yes", "no"):
            continue
        p = norm_pair(*pair)
        if p in scores:
            out[p] = (featurize(scores[p], methods), 1 if lab == "yes" else 0)
    return out


def _metrics(model: Logit, data: Mapping[Pair, tuple[list[float], int]]) -> dict:
    y = [v[1] for v in data.values()]
    p = [model.proba(v[0]) for v in data.values()]
    n = len(y)
    if not n:
        return {"n": 0}
    tp = sum(1 for a, b in zip(y, p) if a == 1 and b >= 0.5)
    fp = sum(1 for a, b in zip(y, p) if a == 0 and b >= 0.5)
    fn = sum(1 for a, b in zip(y, p) if a == 1 and b < 0.5)
    return {"n": n, "positives": sum(y), "auc": auc(y, p), "accuracy": sum((b >= 0.5) == (a == 1) for a, b in zip(y, p)) / n,
            "precision": tp / (tp + fp) if tp + fp else None, "recall": tp / (tp + fn) if tp + fn else None,
            "brier": sum((a - b) ** 2 for a, b in zip(y, p)) / n}


def _classes(data) -> tuple[int, int]:
    pos = sum(v[1] for v in data.values())
    return pos, len(data) - pos


def explain(model: Logit, x: Sequence[float], top: int = 5) -> list[dict]:
    return [{"feature": n, "contribution": round(c, 4), "direction": "za" if c > 0 else "przeciw"}
            for n, c in model.contributions(x)[:top]]


def active_queue(model: Logit, scores: Mapping[Pair, Mapping[str, Optional[float]]], methods: Sequence[str],
                 labeled: set[Pair], *, k: int = 10) -> list[dict]:
    """Nieocenione pary od najbardziej niepewnych (p najbliżej 0,5)."""
    rows = []
    for pair, sc in sorted(scores.items()):
        if pair in labeled:
            continue
        x = featurize(sc, methods)
        p = model.proba(x)
        rows.append({"pair": list(pair), "p": round(p, 4), "uncertainty": round(1 - 2 * abs(p - 0.5), 4),
                     "why": explain(model, x, 3)})
    return sorted(rows, key=lambda r: (-r["uncertainty"], r["pair"]))[:k]


def labels_needed(dev: Mapping[Pair, tuple[list[float], int]], held: Mapping[Pair, tuple[list[float], int]],
                  names: Sequence[str], *, steps: int = 4, seed: int = 0, l2: float = 0.1) -> list[dict]:
    """Krzywa uczenia: AUC na odłożonych rodzinach dla rosnącej liczby ocen z części rozwojowej.
    Pozwala odczytać, ile ręcznych ocen jeszcze poprawia wynik (wzrost między ostatnimi punktami)."""
    items = sorted(dev.items(), key=lambda kv: hashlib.sha256(f"{seed}{kv[0]}".encode()).hexdigest())
    curve = []
    for s in range(1, steps + 1):
        sub = dict(items[: max(2, len(items) * s // steps)])
        pos, neg = _classes(sub)
        if not pos or not neg:
            curve.append({"labels": len(sub), "auc_heldout": None})
            continue
        m = Logit(list(names), l2=l2).fit([v[0] for v in sub.values()], [v[1] for v in sub.values()])
        curve.append({"labels": len(sub), "auc_heldout": _metrics(m, held).get("auc")})
    return curve


def run(scores: Mapping[Pair, Mapping[str, Optional[float]]], labels: Mapping[Pair, str],
        families: Mapping[str, str], methods: Sequence[str], *, frozen: Optional[Mapping] = None, seed: int = 0,
        heldout_fraction: float = 0.3, l2: float = 0.1, queue: int = 10, source: str = "real") -> dict:
    """Pełny przebieg. Zwraca raport; gdy ocen/rodzin za mało — status insufficient_data + czego brakuje (nie udaje wyniku)."""
    frozen = dict(frozen) if frozen else freeze_split(families, seed=seed, heldout_fraction=heldout_fraction)
    split = check_frozen(frozen)
    data = build_dataset(scores, labels, methods)
    dev_pairs, held_pairs = pairs_in_split(data, split, "dev"), pairs_in_split(data, split, "heldout")
    dev = {p: data[p] for p in dev_pairs}
    held = {p: data[p] for p in held_pairs}
    rep: dict[str, Any] = {"source": source, "methods": list(methods), "split_sha256": frozen["sha256"],
                           "pairs": {"labeled": len(data), "dev": len(dev), "heldout": len(held),
                                     "dropped_cross_boundary": len(data) - len(dev) - len(held)}}
    dp, dn = _classes(dev)
    hp, hn = _classes(held)
    missing = []
    if min(dp, dn) < MIN_PER_CLASS_DEV:
        missing.append(f"dev: potrzeba >= {MIN_PER_CLASS_DEV} ocen każdej klasy (jest tak={dp}, nie={dn})")
    if min(hp, hn) < MIN_PER_CLASS_HELD:
        missing.append(f"odłożone rodziny: potrzeba >= {MIN_PER_CLASS_HELD} ocen każdej klasy (jest tak={hp}, nie={hn})")
    if missing:
        rep.update(status="insufficient_data", missing=missing)
        return rep
    names = feature_names(methods)
    model = Logit(names, l2=l2).fit([v[0] for v in dev.values()], [v[1] for v in dev.values()])
    baseline = {m: auc([v[1] for v in held.values()], [v[0][i] for v in held.values()]) for i, m in enumerate(methods)}
    curve = labels_needed(dev, held, names, seed=seed, l2=l2)
    aucs = [c["auc_heldout"] for c in curve if c["auc_heldout"] is not None]
    gain = (aucs[-1] - aucs[-2]) if len(aucs) >= 2 else None
    rep.update(status="ok", model=model.to_json(), dev=_metrics(model, dev), heldout=_metrics(model, held),
               heldout_auc_single_method=baseline, learning_curve=curve,
               manual_labels_hint={"last_step_auc_gain": gain,
                                   "advice": ("krzywa nadal rośnie: warto dodać oceny" if gain is None or gain > 0.01
                                              else "krzywa płaska: kolejne oceny dają mało; zadbać o nowe rodziny")})
    rep["active_queue"] = active_queue(model, scores, methods, set(data), k=queue)
    held_ex = sorted(held.items())[:3]
    rep["examples"] = [{"pair": list(p), "label": v[1], "p": round(model.proba(v[0]), 4), "why": explain(model, v[0])}
                       for p, v in held_ex]
    return rep


def write_report(rep: Mapping, out_dir: str | Path) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    p = out / "learned_pairs.json"
    p.write_text(json.dumps(rep, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return p
