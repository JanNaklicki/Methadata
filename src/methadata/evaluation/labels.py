"""Import ocen par od dwóch anotatorów (§6.2). Osie: szablon, narracja, kampania; wartości tak / nie / nie wiadomo."""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping

AXES = ("shared_template", "shared_narrative", "probable_campaign")
# oś oceny -> relacja kontraktu, której wynik jest z nią porównywany
AXIS_RELATION = {"shared_template": "shared_template", "shared_narrative": "shared_narrative",
                 "probable_campaign": "campaign_candidate"}
YES, NO, UNKNOWN, DISPUTED = "yes", "no", "unknown", "disputed"
_ALIASES = {"tak": YES, "yes": YES, "y": YES, "t": YES, "1": YES,
            "nie": NO, "no": NO, "n": NO, "0": NO,
            "nie wiadomo": UNKNOWN, "unknown": UNKNOWN, "?": UNKNOWN, "nw": UNKNOWN}

Pair = tuple[str, str]


class LabelError(ValueError):
    pass


def norm_pair(a: str, b: str) -> Pair:
    if a == b:
        raise LabelError(f"para złożona z jednego materiału: {a}")
    return (a, b) if a < b else (b, a)


def norm_label(raw: str) -> str:
    """Zwraca yes/no/unknown; nierozpoznana etykieta -> LabelError (nie jest cicho liczona jako „nie”)."""
    key = " ".join(str(raw).strip().lower().split())
    if key not in _ALIASES:
        raise LabelError(f"nieznana etykieta {raw!r}")
    return _ALIASES[key]


@dataclass
class Annotation:
    label: str
    rationale: str = ""


@dataclass
class Reference:
    """Zestaw relacji referencyjnych: axis -> pair -> yes/no/unknown/disputed."""
    labels: dict[str, dict[Pair, str]] = field(default_factory=dict)
    rationale: dict[str, dict[Pair, list[str]]] = field(default_factory=dict)
    invalid: list[dict] = field(default_factory=list)   # wiersze z nierozpoznaną etykietą (tryb nieścisły)
    disagreements: list[dict] = field(default_factory=list)

    def axis(self, axis: str) -> dict[Pair, str]:
        return self.labels.get(axis, {})

    def pairs(self) -> list[Pair]:
        return sorted({p for ax in self.labels.values() for p in ax})

    def to_json(self) -> dict:
        return {"labels": {ax: [[a, b, v] for (a, b), v in sorted(m.items())] for ax, m in self.labels.items()},
                "invalid": self.invalid, "disagreements": self.disagreements}


def read_rows(path: str | Path) -> list[dict]:
    """CSV (pair_a,pair_b,axis,label,rationale) lub JSON (lista obiektów o tych kluczach)."""
    p = Path(path)
    if p.suffix.lower() == ".json":
        rows = json.loads(p.read_text(encoding="utf-8"))
    else:
        with p.open(encoding="utf-8", newline="") as fh:
            rows = list(csv.DictReader(fh))
    return rows


def parse_annotator(rows: Iterable[Mapping], *, strict: bool = True, source: str = "") -> tuple[dict, list[dict]]:
    """-> ({axis: {pair: Annotation}}, invalid). Powtórzona para+oś z różną etykietą -> LabelError."""
    out: dict[str, dict[Pair, Annotation]] = {}
    invalid: list[dict] = []
    for i, r in enumerate(rows, 1):
        axis = (r.get("axis") or "").strip()
        if axis not in AXES:
            raise LabelError(f"{source} wiersz {i}: nieznana oś {axis!r}")
        pair = norm_pair(str(r["pair_a"]).strip(), str(r["pair_b"]).strip())
        try:
            label = norm_label(r.get("label", ""))
        except LabelError as e:
            if strict:
                raise LabelError(f"{source} wiersz {i}: {e}") from None
            invalid.append({"source": source, "row": i, "axis": axis, "pair": list(pair), "label": r.get("label")})
            continue
        prev = out.setdefault(axis, {}).get(pair)
        if prev is not None and prev.label != label:
            raise LabelError(f"{source} wiersz {i}: sprzeczne etykiety tej samej pary {pair} na osi {axis}")
        out[axis][pair] = Annotation(label, str(r.get("rationale") or ""))
    return out, invalid


def merge_annotators(a: Mapping, b: Mapping, adjudication: Mapping[str, Mapping[Pair, str]] | None = None) -> Reference:
    """Zgodne etykiety -> referencja. Rozbieżność (także tak/nie vs nie wiadomo) -> `disputed`, chyba że
    rozstrzygnięto ją w `adjudication`. Para ocenona tylko przez jedną osobę -> `disputed` (brak drugiej oceny)."""
    ref = Reference()
    adjudication = adjudication or {}
    for axis in AXES:
        ma, mb = a.get(axis, {}), b.get(axis, {})
        for pair in sorted(set(ma) | set(mb)):
            la, lb = ma.get(pair), mb.get(pair)
            if la and lb and la.label == lb.label:
                value = la.label
            elif pair in adjudication.get(axis, {}):
                value = adjudication[axis][pair]
            else:
                value = DISPUTED
                ref.disagreements.append({"axis": axis, "pair": list(pair),
                                          "a": la.label if la else None, "b": lb.label if lb else None})
            ref.labels.setdefault(axis, {})[pair] = value
            ref.rationale.setdefault(axis, {})[pair] = [x.rationale for x in (la, lb) if x and x.rationale]
    return ref


def import_annotations(path_a: str | Path, path_b: str | Path, adjudication: str | Path | None = None,
                       *, strict: bool = True) -> Reference:
    pa, ia = parse_annotator(read_rows(path_a), strict=strict, source=str(path_a))
    pb, ib = parse_annotator(read_rows(path_b), strict=strict, source=str(path_b))
    adj: dict[str, dict[Pair, str]] = {}
    if adjudication:
        parsed, _ = parse_annotator(read_rows(adjudication), strict=True, source=str(adjudication))
        adj = {ax: {p: an.label for p, an in m.items()} for ax, m in parsed.items()}
    ref = merge_annotators(pa, pb, adj)
    ref.invalid = ia + ib
    return ref


def load_reference(path: str | Path) -> Reference:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    ref = Reference(invalid=d.get("invalid", []), disagreements=d.get("disagreements", []))
    for ax, items in d["labels"].items():
        ref.labels[ax] = {(a, b): v for a, b, v in items}
    return ref
