"""Pomiar E02 na danych źródłowych (tylko odczyt): sąsiedzi materiału przed/po ukryciu tytułu i wpływ komentarzy.

Użycie: python -m methadata.experiments.lexical.report <katalog BACKUP> [--query p8] [--model out.json]
Dane nie są kopiowane ani zapisywane; wynik to tekst/JSON na stdout."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

from methadata.contracts import Artifact, InputKind, Observation, ids
from methadata.contracts.models import ArtifactRole
from methadata.evaluation.splits import split_families

from .adapters import LexicalTfidfComparator, compare_docs, fit_model, sections, DEFAULT_WEIGHTS
from .text import LexicalTextExtractor

# rodziny wg PLAN_KLASTROWANIA §1 (pomiar odniesienia); pozostałe materiały są własnymi rodzinami
FAMILIES = {"p2": "A", "p7": "A", "p15": "A", "p8": "B", "p11": "B", "p3": "C", "p4": "C"}


class DirReader:
    def __init__(self): self.paths: dict[str, Path] = {}
    def open_bytes(self, ref): return self.paths[ref.artifact_id].read_bytes()


def load(backup: Path, skip: tuple[str, ...] = ("p1",)):
    reader, obs = DirReader(), []
    for d in sorted(p for p in backup.iterdir() if p.is_dir() and p.name not in skip):
        htmls = sorted(d.glob("*.html"))
        if not htmls:
            continue
        data = htmls[0].read_bytes()
        oid = ids.observation_id(d.name, "full_capture")
        sha = ids.sha256_bytes(data)
        a = Artifact(artifact_id=ids.artifact_id(oid, "landing_html", sha), observation_id=oid, role=ArtifactRole.landing_html,
                     local_ref=f"{d.name}/page.html", sha256=sha, size=len(data), media_type="text/html")
        reader.paths[a.artifact_id] = htmls[0]
        obs.append(Observation(observation_id=oid, canonical_id=d.name, source_ref=d.name,
                               input_kind=InputKind.full_capture, artifacts=[a]))
    return obs, reader


def measure(backup: Path, query: str = "p8", seed: int = 0) -> dict:
    obs, reader = load(backup)
    ex = LexicalTextExtractor()
    fm = {o.observation_id: list(ex.extract(o, reader=reader)[0]) for o in obs}
    by = {o.canonical_id: o for o in obs}
    fam = {o.canonical_id: FAMILIES.get(o.canonical_id, o.canonical_id) for o in obs}
    split = split_families(fam, seed=seed)
    dev = [by[c].observation_id for c, s in split.items() if s == "dev"]
    model = fit_model(fm, dev, meta={"split": "dev", "seed": seed, "dev_materials": sorted(c for c, s in split.items() if s == "dev")})
    secs = {c: sections(fm[o.observation_id]) for c, o in by.items()}

    def rank(**kw):
        rows = []
        for c in sorted(by):
            if c == query:
                continue
            r = compare_docs(secs[query], secs[c], model, weights=kw.get("weights", DEFAULT_WEIGHTS),
                             hide=kw.get("hide", ()), merge_comments=kw.get("merge_comments", False))
            rows.append({"id": c, "score": r["score"], "same_family": fam[c] == fam[query], "detail": r})
        return sorted(rows, key=lambda x: (-(x["score"] if x["score"] is not None else -1), x["id"]))

    out = {"query": query, "split": split, "model_meta": model.meta,
           "fields_chars": {c: {f: sum(len(s["text"]) for s in secs[c] if s["field"] == f) for f in ("title", "body", "cta", "comments")} for c in sorted(secs)}}
    for name, kw in {"with_title": {}, "title_hidden": {"hide": ("title",)},
                     "comments_merged": {"merge_comments": True},
                     "comments_only": {"weights": {"comments": 1.0}}}.items():
        out[name] = [{"id": r["id"], "score": r["score"], "same_family": r["same_family"],
                      "phrases": [t["term"] for f in r["detail"]["terms"].values() for t in f][:6]} for r in rank(**kw)]
    return {"model": model, **out}


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(__doc__)
        return 2
    q = argv[argv.index("--query") + 1] if "--query" in argv else "p8"
    res = measure(Path(argv[0]), q)
    model = res.pop("model")
    if "--model" in argv:
        Path(argv[argv.index("--model") + 1]).write_text(model.to_json(), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
