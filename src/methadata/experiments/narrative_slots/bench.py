"""Tabela ról z cytatami i porównanie par. Dane rzeczywiste (katalog BACKUP, tylko odczyt) i próbki syntetyczne
raportowane osobno.

  python -m methadata.experiments.narrative_slots.bench --backup DIR p3 p4 [p8 ...] [--controls] [--json]
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from methadata.contracts import Artifact, InputKind, Observation, ids

from .adapters import NarrativeSlotsComparator, NarrativeSlotsExtractor
from .compare import compare_roles
from .samples import pages
from .slots import SLOTS

LABEL = {"who": "kto", "event": "co się wydarzyło", "promise": "obietnica", "product": "produkt", "call": "wezwanie"}


class MemReader:
    def __init__(self):
        self.d: dict[str, bytes] = {}

    def open_bytes(self, ref):
        return self.d[ref.artifact_id]


def make_observation(name: str, data: bytes, reader: MemReader, tags: Sequence[str] = ()) -> Observation:
    oid = ids.observation_id(name, "full_capture")
    sha = ids.sha256_bytes(data)
    a = Artifact(artifact_id=ids.artifact_id(oid, "landing_html", sha), observation_id=oid, role="landing_html",
                 local_ref=f"{name}.html", sha256=sha, media_type="text/html")
    reader.d[a.artifact_id] = data
    return Observation(observation_id=oid, canonical_id="can-" + name, source_ref=name,
                       input_kind=InputKind.full_capture, artifacts=[a], tags=list(tags))


def load_backup(root: Path, names: Sequence[str], reader: MemReader) -> list[Observation]:
    """Pierwszy plik .html bezpośrednio w katalogu pN (treść traktowana jako dane, nigdy wykonywana)."""
    obs = []
    for n in names:
        f = sorted((root / n).glob("*.html"))
        if f:
            obs.append(make_observation(n, f[0].read_bytes(), reader))
    return obs


def analyse(obs: Sequence[Observation], reader: MemReader) -> tuple[dict, dict, list[dict]]:
    ex, cmp_ = NarrativeSlotsExtractor(), NarrativeSlotsComparator()
    feats = {o.observation_id: list(ex.extract(o, reader=reader)[0]) for o in obs}
    from .adapters import slots_from_features
    slots = {o.canonical_id: slots_from_features(feats[o.observation_id])[0] for o in obs}
    rows = []
    for a, b in itertools.combinations(sorted(obs, key=lambda o: o.canonical_id), 2):
        ev = cmp_.compare(a, b, feats, run_id="bench")[0]
        r = compare_roles(slots[a.canonical_id], slots[b.canonical_id])
        rows.append({"pair": list(ev.pair), "score": ev.score, "role_score": r["role_score"], "name_score": r["name_score"],
                     "frame_match": r["frame_match"], "shared_names": r["shared_names"], "explanation": ev.explanation})
    return feats, slots, rows


def role_table(cid: str, slots: Mapping[str, Any]) -> str:
    out = [f"### {cid}", "", "| rola | klasa | cytat | nazwy własne w cytacie |", "|---|---|---|---|"]
    for s in SLOTS:
        v = slots.get(s)
        if v is None:
            out.append(f"| {LABEL[s]} | – | (puste: brak cytatu) | – |")
        else:
            q = v["quote"].replace("|", "\\|")
            out.append(f"| {LABEL[s]} | {v['cls'] or '–'} | „{q}” | {', '.join(v['names']) or '–'} |")
    return "\n".join(out)


def report(obs: Sequence[Observation], reader: MemReader, title: str) -> tuple[str, list[dict]]:
    _, slots, rows = analyse(obs, reader)
    txt = [f"## {title}", ""]
    txt += [role_table(c, slots[c]) + "\n" for c in sorted(slots)]
    txt += ["| para | układ ról (po masce) | zgodność nazw | rama zgodna | wspólne rdzenie nazw |", "|---|---|---|---|---|"]
    for r in rows:
        f = lambda x: "brak" if x is None else f"{x:.3f}"
        txt.append(f"| {r['pair'][0]} / {r['pair'][1]} | {f(r['role_score'])} | {f(r['name_score'])} | "
                   f"{'tak' if r['frame_match'] else 'nie'} | {', '.join(r['shared_names']) or '–'} |")
    return "\n".join(txt), rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="narrative_slots.bench")
    ap.add_argument("--backup", help="katalog z próbkami pN (tylko odczyt)")
    ap.add_argument("names", nargs="*", help="np. p3 p4")
    ap.add_argument("--controls", action="store_true", help="dodatkowo próbki kontrolne syntetyczne")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    outs, allrows = [], {}
    if a.backup and a.names:
        r = MemReader()
        obs = load_backup(Path(a.backup), a.names, r)
        t, rows = report(obs, r, "Dane rzeczywiste (BACKUP)")
        outs.append(t)
        allrows["real"] = rows
    if a.controls:
        r = MemReader()
        obs = [make_observation(n, h.encode(), r, ["synthetic"]) for n, h in pages().items()]
        t, rows = report(obs, r, "Próbka kontrolna SYNTETYCZNA (nie z korpusu)")
        outs.append(t)
        allrows["controls"] = rows
    print(json.dumps(allrows, ensure_ascii=False, indent=1) if a.json else "\n\n".join(outs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
