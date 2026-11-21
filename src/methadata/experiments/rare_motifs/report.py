"""Pomiar E14 na danych źródłowych (tylko odczyt).

Użycie: python -m methadata.experiments.rare_motifs.report <katalog BACKUP> [--group p2,p7,p15] [--export out.json]
Dane nie są kopiowane; eksport (JSON) zapisuje się tam, gdzie wskazano (poza repozytorium)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from methadata.contracts import Artifact, InputKind, Observation, ids
from methadata.contracts.models import ArtifactRole
from methadata.experiments.lexical.text import LexicalTextExtractor
from methadata.extractors.html import HtmlExtractor

from .matrix import TraceMatrix, dumps
from .motifs import MotifConfig, ablate_rarest, mine_motifs
from .traces import TraceConfig


class _Reader:
    def __init__(self):
        self.paths = {}

    def open_bytes(self, ref):
        return self.paths[ref.artifact_id].read_bytes()


def load(backup: Path, skip=("p1",)):
    """Jeden HTML na katalog materiału (.html lub .htm); tylko odczyt."""
    reader, obs = _Reader(), []
    for d in sorted(p for p in backup.iterdir() if p.is_dir() and p.name not in skip):
        htmls = sorted(list(d.glob("*.html")) + list(d.glob("*.htm")))
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


def measure(backup: Path, group=(), *, trace_cfg: TraceConfig = TraceConfig(), motif_cfg: MotifConfig = MotifConfig(),
            skip=("p1",)) -> dict:
    obs, reader = load(Path(backup), skip=tuple(skip))
    hx, lx = HtmlExtractor(), LexicalTextExtractor()
    fm = {o.observation_id: list(hx.extract(o, reader=reader)[0]) + list(lx.extract(o, reader=reader)[0]) for o in obs}
    m = TraceMatrix.build(obs, fm, trace_cfg, dataset_version=f"BACKUP:{Path(backup).name}:{len(obs)}")
    motifs = mine_motifs(m, motif_cfg)
    res = {"matrix": m, "motifs": motifs, "n": m.n, "traces": len(m.traces),
           "by_kind": {k: sum(1 for t in m.traces.values() if t[0] == k) for k in ("resource", "form", "text")}}
    if group:
        res["ablation"] = ablate_rarest(m, list(group))
    return res


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(__doc__)
        return 2
    group, export = (), None
    if "--group" in argv:
        i = argv.index("--group"); group = tuple(argv[i + 1].split(",")); del argv[i:i + 2]
    mk = 2
    if "--min-kinds" in argv:
        i = argv.index("--min-kinds"); mk = int(argv[i + 1]); del argv[i:i + 2]
    rf = 0.25
    if "--rare-fraction" in argv:
        i = argv.index("--rare-fraction"); rf = float(argv[i + 1]); del argv[i:i + 2]
    if "--export" in argv:
        i = argv.index("--export"); export = argv[i + 1]; del argv[i:i + 2]
    r = measure(Path(argv[0]), group, motif_cfg=MotifConfig(rare_fraction=rf, min_kinds=mk))
    m = r["matrix"]
    print(f"materiały={r['n']} ślady={r['traces']} {r['by_kind']}")
    for mo in r["motifs"][:15]:
        print(f"waga={mo.weight:.2f} wsparcie={mo.support} lift={mo.lift and round(mo.lift, 1)} "
              f"materiały={','.join(mo.materials)}")
        for t in mo.traces:
            print(f"    {m.traces[t][0]:8s} df={m.df(t)} {m.traces[t][1][:90]}")
    if "ablation" in r:
        a = r["ablation"]
        print(f"ablacja: usunięto df={a['removed_df']} «{(a['removed_label'] or '')[:70]}» "
              f"-> podpis {len(a['full'].signature)}->{len(a['reduced'].signature)}, "
              f"poza grupą {len(a['full'].outside_support)}->{len(a['reduced'].outside_support)}, "
              f"odróżnia={a['still_distinguishes']}")
    if export:
        Path(export).write_bytes(dumps(m.export(min_df=2, with_conjunctions=r["motifs"])))
        print("eksport:", export)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
