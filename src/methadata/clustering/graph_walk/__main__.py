"""python -m methadata.clustering.graph_walk BACKUP_DIR [--pair a,b] [--drop-top N] [--max-df N]

Buduje graf E14 z katalogu BACKUP (tylko do odczytu), uruchamia spacery z restartem i Infomap, wypisuje podział,
ranking oraz ścieżkę dla pary (przed i po usunięciu N najpopularniejszych śladów)."""
from __future__ import annotations

import sys
from pathlib import Path


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(__doc__)
        return 2
    opt = {"--pair": None, "--drop-top": "0", "--max-df": None}
    for k in opt:
        if k in argv:
            i = argv.index(k)
            opt[k] = argv[i + 1]
            del argv[i:i + 2]
    from methadata.experiments.rare_motifs.report import measure
    from methadata.experiments.rare_motifs.matrix import TraceMatrix  # noqa: F401
    from .core import build_result, explain_pair, rerun_without_popular
    from methadata.experiments.rare_motifs.report import load
    r = measure(Path(argv[0]))
    m = r["matrix"]
    export = m.export(min_df=1)
    obs, _ = load(Path(argv[0]), skip=("p1",))
    cfg = {"similarity_version": "rare-motifs-matrix-v1", "dataset_version": m.dataset_version, "min_trace_df": 2}
    if opt["--max-df"]:
        cfg["max_trace_df"] = int(opt["--max-df"])
    res = build_result(obs, export, cfg)
    print(f"materiały={len(res.graph.materials)} ślady={len(res.graph.of_trace)} grupy="
          f"{[list(g) for g in res.partition if len(g) > 1]} wykluczone={len(res.excluded)} "
          f"ARI={res.agreement['mean_ari']:.2f}")
    for mat in res.materials:
        top = ", ".join(f"{n.material_id}:{n.score:.3f}" for n in mat.ranking[:3])
        print(f"  {mat.material_id} grupa={mat.cluster_id} ranking: {top}")
    if opt["--pair"]:
        a, b = opt["--pair"].split(",")
        e = explain_pair(res, a, b)
        for p in e["paths"]:
            print("  ścieżka", p.kind, " -> ".join(p.nodes), f"p={p.probability:.4f}")
        if int(opt["--drop-top"]):
            rr = rerun_without_popular(obs, export, cfg, res, a, b, drop_top=int(opt["--drop-top"]))
            print("  po usunięciu", rr["dropped_traces"], "ARI", round(rr["partition_ari"], 3),
                  "ścieżki:", [" -> ".join(p.nodes) for p in (rr["after"] or {"paths": []})["paths"]])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
