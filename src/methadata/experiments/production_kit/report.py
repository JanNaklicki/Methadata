"""Raport E12: wspólne sygnatury zestawu z częstością w korpusie, z bibliotekami i bez nich, z lokalizacją po obu stronach."""
from __future__ import annotations

import argparse
import json
import sys
from itertools import combinations
from typing import Any, Mapping, Sequence

from methadata.contracts.models import ExtractionState

from .compare import Corpus, shared_elements
from .elements import extract_elements
from .extractor import KitExtractor


def collect_features(observations, reader, extractors=None) -> dict[str, list]:
    """Cechy X01 + X02 + E12 dla każdej obserwacji; błędy ekstraktorów zostają statusami (nie przerywają)."""
    from methadata.extractors.code import CodeExtractor
    from methadata.extractors.html import HtmlExtractor
    exs = extractors or [HtmlExtractor(), CodeExtractor(), KitExtractor()]
    fm: dict[str, list] = {}
    for o in observations:
        fm[o.observation_id] = []
        for ex in exs:
            fs, _ = ex.extract(o, reader=reader, optional=None)
            fm[o.observation_id] += list(fs)
    return fm


def _loc(refs) -> list[str]:
    out = []
    for r in refs[:3]:
        loc = getattr(r, "locator", None)
        out.append(f"{r.artifact_id[:12]}" + (f"[{loc.start}:{loc.end}]" if loc is not None and hasattr(loc, "start") else ""))
    return out


def build_report(observations, feature_map: Mapping[str, Sequence], *, rare_frac: float = 0.2, min_shared: int = 2,
                 top: int = 20, dataset_version: str = "") -> dict[str, Any]:
    obs = sorted(observations, key=lambda o: o.canonical_id)
    corpus = Corpus.fit(obs, feature_map, rare_frac=rare_frac, min_shared=min_shared)
    els = {o.observation_id: extract_elements(o, feature_map.get(o.observation_id, [])) for o in obs}
    pairs = []
    for a, b in combinations(obs, 2):
        if a.canonical_id == b.canonical_id:
            continue  # ten sam materiał (np. html i render)
        row: dict[str, Any] = {"pair": [a.canonical_id, b.canonical_id]}
        for name, excl in (("with_libraries", False), ("without_libraries", True)):
            sh = shared_elements(els[a.observation_id], els[b.observation_id], corpus, excl)
            row[name] = [{"kind": x.kind, "label": x.label, "origin": x.origin, "df": df, "N": corpus.n,
                          "where_a": _loc(x.refs), "where_b": _loc(y.refs)} for x, y, df in sh]
        if row["with_libraries"]:
            pairs.append(row)
    pairs.sort(key=lambda r: (-len(r["without_libraries"]), -len(r["with_libraries"]), r["pair"]))
    return {"dataset_version": dataset_version, "N": corpus.n, "rare_max_df": corpus.max_df(), "min_shared": min_shared,
            "pairs_with_shared": len(pairs), "pairs_kit_without_libraries": sum(len(p["without_libraries"]) >= min_shared for p in pairs),
            "pairs": pairs[:top]}


def render(rep: Mapping[str, Any], max_rows: int = 8) -> str:
    lines = [f"E12 zestaw strony: N={rep['N']}, element rzadki gdy df<={rep['rare_max_df']}, min. wspólnych={rep['min_shared']}",
             f"pary ze wspólnym rzadkim elementem: {rep['pairs_with_shared']}; "
             f"pary będące zestawem po odjęciu bibliotek/szablonu: {rep['pairs_kit_without_libraries']}"]
    for p in rep["pairs"]:
        lines.append(f"\n{p['pair'][0]} <-> {p['pair'][1]}")
        for name in ("with_libraries", "without_libraries"):
            lines.append(f"  {'Z bibliotekami' if name == 'with_libraries' else 'Bez bibliotek i szablonu przeglądarki'}: {len(p[name])}")
            order = {"form": 0, "css_sheet": 1, "script": 2, "class": 3}
            shown = sorted(p[name], key=lambda e: (order.get(e["kind"], 9), e["df"], e["label"]))[:max_rows]
            if len(shown) < len(p[name]):
                lines.append(f"    (pokazano {len(shown)} z {len(p[name])}; pełna lista w --json)")
            for e in shown:
                lines.append(f"    {e['kind']:9s} df={e['df']}/{e['N']} [{e['origin']}] {e['label'][:60]}  A:{','.join(e['where_a'])}  B:{','.join(e['where_b'])}")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="methadata.experiments.production_kit")
    ap.add_argument("--store", required=True, help="magazyn z `python -m methadata.ingest import`")
    ap.add_argument("--rare-frac", type=float, default=0.2)
    ap.add_argument("--min-shared", type=int, default=2)
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--json", help="zapisz raport JSON (poza repozytorium)")
    ap.add_argument("--dataset-version", default="")
    a = ap.parse_args(argv)
    from methadata.ingest import Store
    st = Store(a.store)
    obs = st.observations()
    fm = collect_features(obs, st)
    rep = build_report(obs, fm, rare_frac=a.rare_frac, min_shared=a.min_shared, top=a.top, dataset_version=a.dataset_version)
    print(render(rep))
    if a.json:
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump(rep, fh, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
