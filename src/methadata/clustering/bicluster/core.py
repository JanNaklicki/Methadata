"""K08: biklastrowanie macierzy materiał x ślad z E14 - domknięte podmacierze współwystępowania.

Bicluster = para (zbiór materiałów M, zbiór śladów T), gdzie każdy materiał z M ma każdy ślad z T (podmacierz
pełna), a T jest domknięty (to wszystkie ślady wspólne M) i M jest domknięty (to wszystkie materiały mające T).
Grupa wychodzi razem z cechami, które ją definiują: lista materiałów + lista śladów, wsparcie = |M|,
pokrycie = |M| / N korpusu. Wyszukiwanie: LCM (domknięte zbiory częste, prefix-preserving), bez zależności.
Materiał może należeć do wielu biklastrów (Assignment.cluster_ids), o ile biklaster nie jest redundantny
(Jaccard materiałów z lepiej ocenionym >= redundancy_jaccard).
Wejście: eksport E14 (TraceMatrix.export / artefakt graph_edges) albo TraceMatrix; config musi wskazywać
feature_version (wersja cech/podobieństw). Dowód przypisania: krawędzie grafu (feature_ids, evidence_refs).
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from methadata.contracts import (AdapterKind, AdapterSpec, Assignment, AssignmentStatus, ClusterRun, Observation,
                                 PairEvidence, Relation)

METHOD_ID = "bicluster"
METHOD_VERSION = "1"
DEFAULT_RELATION = Relation.campaign_candidate


@dataclass(frozen=True)
class TraceEvidence:
    material: str
    trace: str
    observation_id: str
    feature_ids: tuple[str, ...]
    evidence_refs: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class Bicluster:
    cluster_id: str
    materials: tuple[str, ...]
    traces: tuple[str, ...]
    kinds: tuple[str, ...]            # rodzaje śladów (posortowane, unikalne)
    labels: Mapping[str, str]
    component_df: Mapping[str, int]
    support: int                      # |materials|
    coverage: float                   # support / N
    weight: float                     # suma idf śladów
    lift: Optional[float]             # support / (N * prod df/N)
    evidence: tuple[TraceEvidence, ...]   # krawędzie materiał-ślad, które dały przypisanie

    def to_dict(self) -> dict:
        return {"cluster_id": self.cluster_id, "materials": list(self.materials), "traces": list(self.traces),
                "kinds": list(self.kinds), "support": self.support, "coverage": self.coverage,
                "weight": self.weight, "lift": self.lift, "component_df": dict(self.component_df),
                "labels": dict(self.labels)}


@dataclass(frozen=True)
class BiclusterResult:
    run: ClusterRun
    config: dict[str, Any]
    biclusters: tuple[Bicluster, ...]
    n_materials: int
    n_traces_used: int
    covered: tuple[str, ...]          # materiały w >= 1 biklastrze
    coverage: float                   # |covered| / N
    truncated: bool                   # przerwano z powodu max_nodes

    def pair(self, cluster_id: str) -> tuple[list[str], list[str]]:
        """Para 'lista materiałów + lista cech' dla grupy."""
        b = next(b for b in self.biclusters if b.cluster_id == cluster_id)
        return list(b.materials), list(b.traces)


def resolve_config(config: Mapping[str, Any]) -> dict[str, Any]:
    if config.get("feature_version") in (None, ""):
        raise ValueError("config.feature_version jest wymagane (wejście musi wskazywać wersję cech/podobieństw)")
    out = {"feature_version": str(config["feature_version"]),
           "dataset_version": None if config.get("dataset_version") in (None, "") else str(config["dataset_version"]),
           "relation": Relation(config.get("relation", DEFAULT_RELATION)).value}

    def num(key, default, kind, lo, hi=None):
        v = config.get(key, default)
        if v is None and default is None:
            return None
        if isinstance(v, bool) or not isinstance(v, kind) or v < lo or (hi is not None and v > hi):
            raise ValueError(f"config.{key} poza zakresem")
        return v

    out["min_materials"] = num("min_materials", 2, int, 1)
    out["min_traces"] = num("min_traces", 2, int, 1)
    out["min_kinds"] = num("min_kinds", 1, int, 1)
    out["max_df_fraction"] = float(num("max_df_fraction", 0.5, (int, float), 0.0, 1.0))
    out["redundancy_jaccard"] = num("redundancy_jaccard", 0.8, (int, float), 0.0, 1.0)
    out["max_biclusters"] = num("max_biclusters", 200, int, 1)
    out["max_nodes"] = num("max_nodes", 200_000, int, 1)
    return out


def _digest(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:12]


def _as_export(matrix: Any) -> dict:
    if isinstance(matrix, Mapping):
        if "graph" not in matrix or "traces" not in matrix:
            raise ValueError("wejście nie jest eksportem E14 (brak 'graph'/'traces')")
        return dict(matrix)
    if hasattr(matrix, "export"):
        return matrix.export()
    raise TypeError("oczekiwano eksportu E14 (dict) albo TraceMatrix")


def load_export(data: bytes | str | Mapping[str, Any]) -> dict:
    return _as_export(json.loads(data) if isinstance(data, (bytes, str)) else data)


def load_from_store(store: Any, parent_id: str, origin: str = "rare-motifs-export") -> dict:
    """Eksport E14 zapisany przez store_export (role=graph_edges) -> dict."""
    raw = store.read_derived(parent_id, origin)
    if raw is None:
        raise ValueError(f"brak eksportu E14 dla {parent_id} (origin={origin})")
    return load_export(raw)


def _closed_itemsets(items: Sequence[str], post: Mapping[str, int], all_mask: int, min_sup: int, max_nodes: int):
    """LCM: domknięte zbiory śladów o wsparciu >= min_sup. Zwraca (lista (indeksy śladów, maska materiałów), obcięto?)."""
    n = len(items)
    pc = [post[t] for t in items]

    def closure(mask: int) -> tuple[int, ...]:
        return tuple(j for j in range(n) if mask & pc[j] == mask)

    out: list[tuple[tuple[int, ...], int]] = []
    nodes = 0
    truncated = False
    root = closure(all_mask)
    out.append((root, all_mask))
    stack = [(root, all_mask, -1)]
    while stack:
        p, mask, core = stack.pop()
        pset = set(p)
        for e in range(core + 1, n):
            if e in pset:
                continue
            m2 = mask & pc[e]
            if m2.bit_count() < min_sup:
                continue
            nodes += 1
            if nodes > max_nodes:
                return out, True
            q = closure(m2)
            if tuple(j for j in q if j < e) != tuple(j for j in p if j < e):
                continue
            out.append((q, m2))
            stack.append((q, m2, e))
    return out, truncated


def _mask_members(mask: int, mats: Sequence[str]) -> tuple[str, ...]:
    return tuple(m for i, m in enumerate(mats) if mask >> i & 1)


def build_result(matrix: Any, config: Mapping[str, Any], *, seed: int = 0,
                 observations: Optional[Sequence[Observation]] = None) -> BiclusterResult:
    cfg = resolve_config(config)
    exp = _as_export(matrix)
    cfg["dataset_version"] = cfg["dataset_version"] or exp.get("dataset_version") or None
    if not cfg["dataset_version"]:
        raise ValueError("brak dataset_version (config ani eksport E14)")
    mats = list(exp["matrix"]["rows"])
    N = len(mats)
    if observations is not None:
        ocids = {o.canonical_id for o in observations}
        stray = sorted(set(mats) - ocids)
        if stray:
            raise ValueError(f"macierz E14 zawiera materiały spoza obserwacji: {stray[:3]}")
        all_mats = sorted(ocids)
    else:
        all_mats = sorted(mats)
    midx = {m: i for i, m in enumerate(mats)}
    tinfo = {t["trace_id"]: t for t in exp["traces"]}
    post: dict[str, int] = {}
    ev: dict[tuple[str, str], TraceEvidence] = {}
    for e in exp["graph"]["edges"]:
        post[e["trace"]] = post.get(e["trace"], 0) | (1 << midx[e["material"]])
        ev[(e["material"], e["trace"])] = TraceEvidence(
            e["material"], e["trace"], e.get("observation_id", ""), tuple(e.get("feature_ids", ())),
            tuple(e.get("evidence_refs", ())))
    has = {e[0] for e in ev}
    max_df = max(cfg["min_materials"], math.floor(cfg["max_df_fraction"] * N)) if N else 0
    items = sorted(t for t, m in post.items() if cfg["min_materials"] <= m.bit_count() <= max_df)
    all_mask = (1 << N) - 1
    # korzeń: wspólne wszystkim; gdy N=0 nic do roboty
    raw, truncated = ([], False) if not N or not items else _closed_itemsets(items, post, all_mask,
                                                                          cfg["min_materials"], cfg["max_nodes"])
    cand = []
    for idx, mask in raw:
        ts = tuple(items[j] for j in idx)
        kinds = tuple(sorted({tinfo[t]["kind"] for t in ts}))
        if len(ts) < cfg["min_traces"] or len(kinds) < cfg["min_kinds"] or mask.bit_count() < cfg["min_materials"]:
            continue
        members = _mask_members(mask, mats)
        idf = {t: math.log(N / post[t].bit_count()) for t in ts}
        expected = N * math.prod(post[t].bit_count() / N for t in ts)
        cand.append(Bicluster(
            cluster_id="bc-" + _digest([members, ts]), materials=members, traces=ts, kinds=kinds,
            labels={t: tinfo[t]["label"] for t in ts}, component_df={t: post[t].bit_count() for t in ts},
            support=len(members), coverage=len(members) / N, weight=sum(idf.values()),
            lift=(len(members) / expected) if expected > 0 else None,
            evidence=tuple(ev[(m, t)] for m in members for t in ts)))
    cand.sort(key=lambda b: (-b.weight, -b.support, b.traces))
    kept: list[Bicluster] = []
    thr = cfg["redundancy_jaccard"]
    for b in cand:
        sb = set(b.materials)
        if thr is not None and any(len(sb & set(k.materials)) / len(sb | set(k.materials)) >= thr for k in kept):
            continue
        kept.append(b)
        if len(kept) >= cfg["max_biclusters"]:
            break
    gid: dict[str, list[str]] = {}
    for b in kept:
        for m in b.materials:
            gid.setdefault(m, []).append(b.cluster_id)
    assignments = []
    for m in all_mats:
        if m in gid:
            assignments.append(Assignment(material_id=m, cluster_ids=sorted(gid[m]), status=AssignmentStatus.grouped))
        elif m in has:
            assignments.append(Assignment(material_id=m, status=AssignmentStatus.singleton))
        else:
            assignments.append(Assignment(material_id=m, status=AssignmentStatus.insufficient_data))
    run_cfg = dict(cfg)
    run_cfg.pop("dataset_version")
    rel = Relation(cfg["relation"])
    run = ClusterRun(
        run_id=f"run-{METHOD_ID}-" + _digest([cfg, seed, [(b.materials, b.traces) for b in kept], all_mats]),
        dataset_version=cfg["dataset_version"], method_id=METHOD_ID, method_version=METHOD_VERSION,
        config=run_cfg, seed=seed, relation=rel, assignments=assignments)
    return BiclusterResult(run=run, config=cfg, biclusters=tuple(kept), n_materials=N, n_traces_used=len(items),
                           covered=tuple(sorted(gid)), coverage=len(gid) / N if N else 0.0, truncated=truncated)


def compare_with_e14(result: BiclusterResult, motifs: Sequence[Any]) -> dict[str, Any]:
    """Porównanie z motywami E14 (Motif lub jego to_dict): dla każdego motywu najlepiej pasujący biklaster
    (największy Jaccard materiałów); exact = te same materiały, traces_covered = motyw ⊆ ślady biklastra."""
    rows = []
    for mo in motifs:
        d = mo.to_dict() if hasattr(mo, "to_dict") else dict(mo)
        mm, tt = set(d["materials"]), set(d["traces"])
        best, bj = None, 0.0
        for b in result.biclusters:
            j = len(mm & set(b.materials)) / len(mm | set(b.materials))
            if j > bj or (j == bj and best is not None and b.cluster_id < best.cluster_id):
                best, bj = b, j
        rows.append({"motif_traces": sorted(tt), "motif_materials": sorted(mm), "matched": best is not None,
                     "cluster_id": best.cluster_id if best else None, "jaccard": bj,
                     "exact": bool(best) and set(best.materials) == mm,
                     "traces_covered": bool(best) and tt <= set(best.traces),
                     "extra_traces": sorted(set(best.traces) - tt) if best else []})
    n = len(rows)
    return {"motifs": n, "matched": sum(r["matched"] for r in rows), "exact": sum(r["exact"] for r in rows),
            "traces_covered": sum(r["traces_covered"] for r in rows), "rows": rows,
            "bicluster_only": [b.cluster_id for b in result.biclusters
                               if not any(r["cluster_id"] == b.cluster_id and r["exact"] for r in rows)]}


class BiclusterClusterer:
    spec = AdapterSpec(id="bicluster-v1", kind=AdapterKind.clusterer, version=METHOD_VERSION,
                       description="Biklastrowanie materiał x ślad (E14): domknięte podmacierze współwystępowania")

    def cluster(self, observations: Sequence[Observation], pair_evidence: Sequence[PairEvidence],
                config: Mapping[str, Any], *, seed: int = 0, optional: Mapping[str, Any] | None = None) -> ClusterRun:
        matrix = (optional or {}).get("trace_matrix")
        if matrix is None:
            # runner wywołuje każdy clusterer; bez macierzy E14 nie ma czego grupować (nie zgłaszamy błędu)
            rel = Relation(config.get("relation", DEFAULT_RELATION))
            mats = sorted({o.canonical_id for o in observations})
            return ClusterRun(
                run_id=f"run-{METHOD_ID}-" + _digest([dict(config), seed, mats, "no-matrix"]),
                dataset_version=str(config.get("dataset_version") or "unknown"), method_id=METHOD_ID,
                method_version=METHOD_VERSION, config={"relation": rel.value, "no_matrix": True}, seed=seed,
                relation=rel,
                assignments=[Assignment(material_id=m, status=AssignmentStatus.insufficient_data) for m in mats])
        return build_result(matrix, config, seed=seed, observations=observations).run


def get_adapters():
    return [BiclusterClusterer()]


ADAPTERS = get_adapters()
