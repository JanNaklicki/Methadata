"""K10: Similarity Network Fusion (Wang i in., Nature Methods 2014) osobnych sieci podobieństwa: tekst, obraz, struktura.

Przebieg:
  1. Dla każdego widoku (rodziny dowodów, jedna metoda komparatora) powstaje macierz podobieństwa W_v z PairEvidence
     (najlepszy wynik pary; brak wyniku = brak pomiaru, liczony jako 0 w macierzy i raportowany osobno).
  2. Podzbiór z kompletnymi widokami: materiał "ma widok", gdy ma co najmniej jeden wykonany pomiar (score != None)
     w tym widoku. Domyślnie (subset="complete") fuzja obejmuje tylko materiały mające wszystkie widoki; reszta
     dostaje status insufficient_data i jest wyszczególniona (brak widoku != podobieństwo 0).
  3. SNF: P_v = W_v/(2 Σ W_v) z przekątną 1/2; S_v = jądro K najbliższych sąsiadów; iteracja
     P_v <- S_v (średnia pozostałych P_u) S_v^T, T razy; wynik = średnia P_v. Implementacja na numpy (extra `density`),
     import leniwy.
  4. Fuzja jest przeskalowana do [0,1] dzieleniem przez największą wartość pozaprzekątniową; to jest wynik pary
     (PairEvidence relation=campaign_candidate, method_id=snf-v1). Wartość bez dyfuzji (T=0, średnia znormalizowanych
     widoków, to samo skalowanie) jest liczona obok jako "direct".
  5. Krawędź wzmocniona przez sąsiedztwo: direct < próg <= fuzja. Jej ślady: sąsiedzi pośredniczący m (siła
     Σ_v min(S_v[i,m], S_v[j,m]) (wspólny sąsiad w jądrach kNN obu końców)) wraz z oryginalnymi PairEvidence (i,m) oraz (m,j) i ich evidence_refs.
  6. Grupowanie sieci wynikowej: components (K01), spectral (K06) albo leiden (K05) na PairEvidence snf-v1.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from itertools import combinations
from typing import Any, Iterable, Mapping, Optional, Sequence

from methadata.contracts import (AdapterKind, AdapterSpec, ArtifactRef, Assignment, AssignmentStatus, ClusterRun,
                                 Observation, PairEvidence, Relation)

METHOD_ID = "snf-v1"
METHOD_VERSION = "1"
RELATION = Relation.campaign_candidate
CLUSTERERS = ("components", "spectral", "leiden")
SUBSETS = ("complete", "all")


# ------------------------------------------------------------------ konfiguracja

def resolve_config(config: Mapping[str, Any], *, for_run: bool = True) -> dict[str, Any]:
    views = config.get("views")
    if not isinstance(views, Mapping) or len(views) < 2:
        raise ValueError("config.views jest wymagane: co najmniej dwa widoki (nazwa -> {method_id})")
    required = ["similarity_version"] + (["dataset_version"] if for_run else [])
    for key in required:
        if config.get(key) in (None, ""):
            raise ValueError(f"config.{key} jest wymagane (wejście musi wskazywać wersję cech/podobieństw)")
    out_views, seen = {}, set()
    for name in sorted(views):
        mid = views[name].get("method_id")
        if not mid:
            raise ValueError(f"config.views.{name}.method_id jest wymagane")
        if mid in seen:
            raise ValueError(f"metoda {mid} przypisana do dwóch widoków")
        seen.add(mid)
        rel = views[name].get("relation")
        out_views[name] = {"method_id": str(mid), "relation": Relation(rel).value if rel else None}

    def num(name, v, lo, hi, lo_open=False):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not ((lo < v if lo_open else lo <= v) and v <= hi):
            raise ValueError(f"config.{name} poza zakresem: {v!r}")
        return float(v)

    def integer(name, v, lo):
        if isinstance(v, bool) or not isinstance(v, int) or v < lo:
            raise ValueError(f"config.{name} musi być liczbą całkowitą >= {lo}")
        return v

    subset = config.get("subset", "complete")
    if subset not in SUBSETS:
        raise ValueError(f"config.subset musi być jednym z {SUBSETS}")
    if config.get("scale", "local") not in ("local", "global"):
        raise ValueError("config.scale musi być 'local' albo 'global'")
    clusterer = config.get("clusterer", "components")
    if clusterer not in CLUSTERERS:
        raise ValueError(f"config.clusterer musi być jednym z {CLUSTERERS}")
    fus = dict(config.get("fusion") or {})
    cfg = {"views": out_views, "subset": subset,
           "neighbors": integer("neighbors", config.get("neighbors", 5), 1),
           "iterations": integer("iterations", config.get("iterations", 20), 0),
           "threshold": num("threshold", config.get("threshold", 0.5), 0.0, 1.0, lo_open=True),
           "scale": config.get("scale", "local"),
           "min_view_mean": num("min_view_mean", config.get("min_view_mean", 0.2), 0.0, 1.0),
           "top_via": integer("top_via", config.get("top_via", 3), 0),
           "clusterer": clusterer, "clusterer_config": dict(config.get("clusterer_config") or {}),
           "fusion": {"min_support": integer("fusion.min_support", fus.get("min_support", 2), 1),
                      "coverage_min": num("fusion.coverage_min", fus.get("coverage_min", 0.5), 0.0, 1.0),
                      "support_min": num("fusion.support_min", fus.get("support_min", 0.5), 0.0, 1.0, lo_open=True)},
           "similarity_version": str(config["similarity_version"])}
    if for_run:
        cfg["dataset_version"] = str(config["dataset_version"])
    return cfg


def _np():
    try:
        import numpy
    except ImportError as exc:
        raise RuntimeError("SNF wymaga extra `density`: pip install 'methadata[density]' (numpy)") from exc
    return numpy


# ------------------------------------------------------------------ SNF (numpy)

def _normalize(W):
    """Wang i in.: P_ij = W_ij / (2 Σ_{k!=i} W_ik), P_ii = 1/2 (wiersz zerowy: tylko przekątna)."""
    np = _np()
    W = np.array(W, dtype=float)
    np.fill_diagonal(W, 0.0)
    s = W.sum(axis=1, keepdims=True)
    s[s == 0] = 1.0
    P = W / (2.0 * s)
    np.fill_diagonal(P, 0.5)
    return P


def _local_kernel(W, k: int):
    """S_ij = W_ij / Σ_{l in N_i} W_il dla j z K najsilniejszych sąsiadów i (remis: mniejszy indeks)."""
    np = _np()
    n = W.shape[0]
    W = np.array(W, dtype=float)
    np.fill_diagonal(W, 0.0)
    S = np.zeros_like(W)
    for i in range(n):
        order = sorted((j for j in range(n) if j != i and W[i, j] > 0), key=lambda j: (-W[i, j], j))[:k]
        tot = sum(W[i, j] for j in order)
        for j in order:
            S[i, j] = W[i, j] / tot
    np.fill_diagonal(S, 0.0)
    return S


def _scaled(F, mode: str = "local"):
    """Fuzja do [0,1] (przekątna zerowana). "local": F_ij / max(najsilniejsze powiązanie i, najsilniejsze j), dzięki
    czemu członkowie dużej grupy nie są karani za dzielenie masy SNF między wielu sąsiadów; "global": dzielenie przez
    największą wartość pozaprzekątniową."""
    np = _np()
    F = np.array(F, dtype=float)
    np.fill_diagonal(F, 0.0)
    if mode == "global":
        m = F.max() if F.size else 0.0
        return F / m if m > 0 else F
    rm = F.max(axis=1)
    den = np.maximum(rm[:, None], rm[None, :])
    den[den == 0] = 1.0
    return np.clip(F / den, 0.0, 1.0)


def snf_matrix(views: Sequence[Any], *, k: int, iterations: int, scale: str = "local"):
    """-> (fuzja przeskalowana, direct przeskalowany, [S_v], [P0_v]). views: macierze symetryczne (n x n)."""
    np = _np()
    n = views[0].shape[0]
    k = max(1, min(k, n - 1))
    P0 = [_normalize((W + W.T) / 2) for W in views]
    S = [_local_kernel((W + W.T) / 2, k) for W in views]
    P = [p.copy() for p in P0]
    m = len(views)
    for _ in range(iterations):
        new = []
        for v in range(m):
            other = sum(P[u] for u in range(m) if u != v) / (m - 1)
            q = S[v] @ other @ S[v].T
            q = _normalize(q)
            new.append((q + q.T) / 2)
        P = new
    fused = _normalize(sum(P) / m)
    fused = (fused + fused.T) / 2
    direct = _normalize(sum(P0) / m)
    return _scaled(fused, scale), _scaled((direct + direct.T) / 2, scale), S, P0


# ------------------------------------------------------------------ widoki z PairEvidence

def _pick_best(evs: Iterable[PairEvidence]) -> Optional[PairEvidence]:
    best = None
    for e in sorted(evs, key=lambda e: e.pair_evidence_id):
        if best is None or (e.score is not None and (best.score is None or e.score > best.score)):
            best = e
    return best


def _view_evidence(mats: Sequence[str], pair_evidence: Sequence[PairEvidence], cfg: Mapping[str, Any]):
    """-> {widok: {(a,b): najlepszy PairEvidence}}; dowody spoza wskazanych metod są pomijane."""
    matset = set(mats)
    by_method = {v["method_id"]: (name, v["relation"]) for name, v in cfg["views"].items()}
    groups: dict[str, dict[tuple[str, str], list[PairEvidence]]] = {n: {} for n in cfg["views"]}
    for e in pair_evidence:
        hit = by_method.get(e.method_id)
        if hit is None or (hit[1] is not None and hit[1] != e.relation.value):
            continue
        for c in e.pair:
            if c not in matset:
                raise ValueError(f"PairEvidence {e.pair_evidence_id} wskazuje materiał spoza obserwacji: {c}")
        groups[hit[0]].setdefault(e.pair, []).append(e)
    return {n: {p: _pick_best(v) for p, v in g.items()} for n, g in groups.items()}


def view_coverage(mats: Sequence[str], best: Mapping[str, Mapping[tuple[str, str], PairEvidence]]):
    """-> {widok: zbiór materiałów z co najmniej jednym wykonanym pomiarem}."""
    out = {}
    for name, g in best.items():
        have: set[str] = set()
        for p, e in g.items():
            if e is not None and e.score is not None:
                have.update(p)
        out[name] = have & set(mats)
    return out


def _r(x: Optional[float]) -> Optional[float]:
    return None if x is None else round(float(x), 6)


# ------------------------------------------------------------------ wynik

@dataclass(frozen=True)
class SnfResult:
    run: ClusterRun
    config: dict[str, Any]
    pairs: tuple[PairEvidence, ...]                 # fuzje par (snf-v1) w podzbiorze
    subset: tuple[str, ...]                          # materiały objęte fuzją
    excluded: dict[str, list[str]]                   # materiał -> brakujące widoki
    view_stats: dict[str, dict[str, Any]]            # per widok: materiały z widokiem, krawędzie >= próg, niezmierzone pary
    view_edges: dict[str, tuple[tuple[str, str, float], ...]]   # sieci per widok (pary >= próg)
    details: dict[tuple[str, str], dict[str, Any]]   # szczegóły par (widoki, direct, fuzja, wzmocnienie, ślady)
    amplified: tuple[tuple[str, str], ...]
    clusters: tuple[Any, ...]                        # ClusterEvidence z wybranego klastrowania
    clustering: Any                                  # surowy wynik clusterera (K01/K06/K05)
    fused: Any                                       # macierz fuzji (numpy), kolejność = subset
    direct: Any

    def edge_traces(self, a: str, b: str) -> dict[str, Any]:
        """Oryginalne ślady krawędzi: oryginalne dowody widoków, sąsiedzi pośredniczący i ich dowody z evidence_refs."""
        return self.details[tuple(sorted((a, b)))]

    def groups(self) -> list[tuple[str, ...]]:
        return sorted(tuple(sorted(c.members)) for c in self.clusters)


def _ref_json(r: ArtifactRef) -> dict[str, Any]:
    return json.loads(r.model_dump_json())


def _leg(name: str, e: Optional[PairEvidence]) -> dict[str, Any]:
    return {"view": name, "pair": list(e.pair) if e else None, "score": _r(e.score) if e else None,
            "pair_evidence_id": e.pair_evidence_id if e else None}


def snf_cluster(observations: Sequence[Observation], pair_evidence: Sequence[PairEvidence],
                config: Mapping[str, Any], *, seed: int = 0) -> SnfResult:
    np = _np()
    cfg = resolve_config(config)
    all_mats = sorted({o.canonical_id for o in observations})
    best = _view_evidence(all_mats, pair_evidence, cfg)
    have = view_coverage(all_mats, best)
    names = sorted(cfg["views"])
    missing = {m: [v for v in names if m not in have[v]] for m in all_mats}
    if cfg["subset"] == "complete":
        mats = [m for m in all_mats if not missing[m]]
    else:
        mats = [m for m in all_mats if len(missing[m]) < len(names)]
    excluded = {m: missing[m] for m in all_mats if m not in set(mats)}
    n = len(mats)
    idx = {m: i for i, m in enumerate(mats)}
    run_id = "run-snf-" + hashlib.sha256(json.dumps(
        [cfg, seed, mats, sorted(e.pair_evidence_id for e in pair_evidence)], sort_keys=True,
        default=str).encode()).hexdigest()[:12]

    Ws, view_stats, view_edges = [], {}, {}
    for name in names:
        W = np.zeros((n, n))
        unmeasured, edges = 0, []
        for (a, b), e in best[name].items():
            if a in idx and b in idx and e is not None and e.score is not None:
                W[idx[a], idx[b]] = W[idx[b], idx[a]] = e.score
                if e.score >= cfg["threshold"]:
                    edges.append((a, b, float(e.score)))
        unmeasured = sum(1 for a, b in combinations(mats, 2)
                         if best[name].get((a, b)) is None or best[name][(a, b)].score is None)
        Ws.append(W)
        view_edges[name] = tuple(sorted(edges))
        view_stats[name] = {"method_id": cfg["views"][name]["method_id"], "materials_with_view": len(have[name]),
                            "subset_materials": n, "edges_at_threshold": len(edges), "unmeasured_pairs": unmeasured}

    details: dict[tuple[str, str], dict[str, Any]] = {}
    pairs: list[PairEvidence] = []
    amplified: list[tuple[str, str]] = []
    fused = direct = np.zeros((n, n))
    if n >= 3:
        fused, direct, S, _ = snf_matrix(Ws, k=cfg["neighbors"], iterations=cfg["iterations"],
                                    scale=cfg["scale"])
    thr = cfg["threshold"]
    top_via = cfg["top_via"]
    for a, b in combinations(mats, 2):
        i, j = idx[a], idx[b]
        legs = {nm: best[nm].get((a, b)) for nm in names}
        direct_refs = {a: [], b: []}
        for e in legs.values():
            if e is not None and e.score is not None:
                for c in (a, b):
                    for r in e.evidence_refs.get(c, []):
                        if r not in direct_refs[c]:
                            direct_refs[c].append(r)
        f, d = float(fused[i, j]), float(direct[i, j])
        is_amp = n >= 3 and d < thr <= f
        via = []
        if n >= 3 and top_via and (is_amp or f >= thr):
            cand = []
            for m in mats:
                if m in (a, b):
                    continue
                q = idx[m]
                strength = sum(min(S[v][i, q], S[v][j, q]) for v in range(len(names)))
                if strength > 0:
                    cand.append((-strength, m))
            for neg, m in sorted(cand)[:top_via]:
                via.append({"via": m, "strength": _r(-neg),
                            "legs_a": [_leg(nm, best[nm].get(tuple(sorted((a, m))))) for nm in names],
                            "legs_b": [_leg(nm, best[nm].get(tuple(sorted((m, b))))) for nm in names]})
        refs = {a: list(direct_refs[a]), b: list(direct_refs[b])}
        if is_amp:   # ślady wzmocnienia: oryginalne dowody par (a,m) i (m,b) po stronie a i b
            for v in via:
                for side, key, c in (("legs_a", "a", a), ("legs_b", "b", b)):
                    for lg in v[side]:
                        e = best[lg["view"]].get(tuple(sorted(lg["pair"]))) if lg["pair"] else None
                        if e is not None and e.score is not None:
                            for r in e.evidence_refs.get(c, []):
                                if r not in refs[c]:
                                    refs[c].append(r)
        measured = sum(1 for e in legs.values() if e is not None and e.score is not None)
        has_refs = bool(refs[a] and refs[b])
        view_mean = sum((legs[nm].score if legs[nm] is not None and legs[nm].score is not None else 0.0)
                        for nm in names) / len(names)
        floor_ok = view_mean >= cfg["min_view_mean"]
        score = (round(f, 12) if floor_ok else min(f, math.nextafter(thr, 0.0))) \
            if (has_refs and (measured or is_amp or via)) else None
        det = {"pair": [a, b], "fused": _r(f), "direct": _r(d), "amplified": bool(is_amp and score is not None and floor_ok), "view_mean": _r(view_mean),
               "below_view_floor": not floor_ok,
               "views": {nm: {"method_id": cfg["views"][nm]["method_id"], "score": _r(legs[nm].score) if legs[nm] else None,
                              "pair_evidence_id": legs[nm].pair_evidence_id if legs[nm] else None} for nm in names},
               "measured_views": measured, "via": via,
               "refs": {c: [_ref_json(r) for r in refs[c]] for c in (a, b)}}
        details[(a, b)] = det
        if det["amplified"]:
            amplified.append((a, b))
        expl = (f"SNF {METHOD_ID}: wynik {_r(score)} (fuzja {_r(f)}, bez dyfuzji {_r(d)}, próg {thr}, K={cfg['neighbors']}, "
                f"T={cfg['iterations']}); widoki: " +
                ", ".join(f"{nm}={det['views'][nm]['score']}" for nm in names) +
                ("; krawędź wzmocniona przez sąsiedztwo (bez dyfuzji poniżej progu); sąsiedzi pośredniczący: " +
                 ", ".join(v["via"] for v in via) if det["amplified"] else "") + ". Szczegóły: " +
                json.dumps({k: det[k] for k in ("fused", "direct", "amplified", "view_mean", "views", "via")}, sort_keys=True,
                           ensure_ascii=False))
        pairs.append(PairEvidence(run_id=run_id, pair=(a, b), method_id=METHOD_ID, relation=RELATION, score=score,
                                  evidence_refs=refs if score is not None else {}, explanation=expl))

    sub_obs = [o for o in observations if o.canonical_id in idx]
    ccfg = {"similarity_version": cfg["similarity_version"], "dataset_version": cfg["dataset_version"],
            "relation": RELATION.value, "method_id": METHOD_ID, "threshold": thr}
    clusterer = cfg["clusterer"]
    extra = dict(cfg["clusterer_config"])
    if clusterer == "components":
        from methadata.clustering.components import build_result
        res = build_result(sub_obs, pairs, {**ccfg, **extra}, seed=seed)
    elif clusterer == "spectral":
        from methadata.clustering.spectral import core as sp
        res = sp.build_result(sub_obs, pairs, {**{k: v for k, v in ccfg.items() if k != "threshold"},
                                               "min_edge_weight": thr, **extra}, seed=seed)
    else:
        from methadata.clustering.leiden import core as ld
        res = ld.build_result(sub_obs, pairs, {**{k: v for k, v in ccfg.items() if k != "threshold"},
                                               "min_edge_weight": thr, **extra}, seed=seed)
    asg = list(res.run.assignments) + [Assignment(material_id=m, status=AssignmentStatus.insufficient_data)
                                       for m in sorted(excluded)]
    run = res.run.model_copy(update={
        "run_id": run_id, "method_id": METHOD_ID, "method_version": METHOD_VERSION,
        "assignments": sorted(asg, key=lambda a: a.material_id),
        "config": {**res.run.config, "snf": {k: v for k, v in cfg.items() if k != "dataset_version"},
                   "clusterer_method": res.run.method_id, "input_pair_evidence": len(pair_evidence),
                   "subset_materials": n, "excluded_missing_views": {k: v for k, v in sorted(excluded.items())}}})
    return SnfResult(run, cfg, tuple(pairs), tuple(mats), excluded, view_stats, view_edges, details,
                     tuple(amplified), tuple(res.clusters), res, fused, direct)


# ------------------------------------------------------------------ porównanie z K09

def _co_pairs(groups: Iterable[Iterable[str]]) -> set[tuple[str, str]]:
    return {p for g in groups for p in combinations(sorted(g), 2)}


def compare_with_fusion(result: SnfResult, observations: Sequence[Observation],
                        pair_evidence: Sequence[PairEvidence], *, seed: int = 0) -> dict[str, Any]:
    """K09 (fuse_and_cluster) na dokładnie tym samym podzbiorze materiałów i tych samych dowodach składowych."""
    from methadata.clustering.fusion import core as fu
    cfg = result.config
    sub = set(result.subset)
    fam = {nm: {"method_id": v["method_id"], "weight": 1.0, "origin": nm, "support_min": cfg["fusion"]["support_min"],
                **({"relation": v["relation"]} if v["relation"] else {})} for nm, v in cfg["views"].items()}
    fcfg = {"families": fam, "threshold": cfg["threshold"], "coverage_min": cfg["fusion"]["coverage_min"],
            "min_support": min(cfg["fusion"]["min_support"], len(fam)),
            "similarity_version": cfg["similarity_version"], "dataset_version": cfg["dataset_version"]}
    obs = [o for o in observations if o.canonical_id in sub]
    raw = [e for e in pair_evidence if set(e.pair) <= sub]
    k09 = fu.fuse_and_cluster(obs, raw, fcfg, seed=seed)
    g_snf = sorted(tuple(sorted(g)) for g in result.groups())
    g_k09 = sorted(tuple(sorted(c.members)) for c in k09.clusters)
    all_pairs = set(combinations(sorted(sub), 2))
    a, b = _co_pairs(g_snf), _co_pairs(g_k09)
    rand = (len(all_pairs) - len(a ^ b)) / len(all_pairs) if all_pairs else 1.0
    rows = []
    for p in sorted(all_pairs):
        pf = k09.fusions.get(p)
        s_acc = p in {tuple(sorted((e.source, e.target))) for e in result.run.edges}
        det = result.details[p]
        k_acc = bool(pf and pf.status == "ok" and pf.score is not None and pf.score >= cfg["threshold"])
        if s_acc != k_acc:
            rows.append({"pair": list(p), "snf_edge": s_acc, "k09_edge": k_acc, "snf_score": det["fused"],
                         "snf_direct": det["direct"], "snf_amplified": det["amplified"],
                         "k09_score": _r(pf.score) if pf else None, "k09_status": pf.status if pf else None,
                         "k09_supporting_origins": pf.supporting_origins if pf else None,
                         "views": {k: v["score"] for k, v in det["views"].items()}})
    return {"subset": sorted(sub), "snf_groups": g_snf, "k09_groups": g_k09, "identical": g_snf == g_k09,
            "rand_index": round(rand, 6), "snf_only_pairs": sorted(a - b), "k09_only_pairs": sorted(b - a),
            "edge_differences": rows, "k09_config": {k: v for k, v in fcfg.items() if k != "dataset_version"},
            "k09_run_id": k09.run.run_id, "snf_run_id": result.run.run_id}


# ------------------------------------------------------------------ adapter klastrujący

class SnfClusterer:
    spec = AdapterSpec(id=METHOD_ID, kind=AdapterKind.clusterer, version=METHOD_VERSION,
                       description="Similarity Network Fusion sieci tekstu, obrazu i struktury, potem grupowanie sieci wynikowej")

    def cluster(self, observations: Sequence[Observation], pair_evidence: Sequence[PairEvidence],
                config: Mapping[str, Any], *, seed: int = 0, optional: Mapping[str, Any] | None = None) -> ClusterRun:
        return snf_cluster(observations, pair_evidence, config, seed=seed).run


def get_adapters():
    return [SnfClusterer()]
