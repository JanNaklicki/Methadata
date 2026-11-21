"""K02: aglomeracja hierarchiczna average-link (UPGMA) i complete-link na odległościach d = 1 - podobieństwo.

Działa na pełnej porównywalnej macierzy: algorytm wymaga wykonanego porównania (score != None) każdej pary.
Materiały bez żadnego wykonanego porównania oraz te, które zostają bez porównywalnych par, są wykluczane z podaniem
powodu (status insufficient_data) - brakującej pary nie traktujemy ani jak 0, ani jak 1. Zachłanne wykluczanie:
usuwamy materiał z największą liczbą braków (remis: mniejszy canonical_id) aż macierz będzie pełna.

Dendrogram budowany jest w całości (wszystkie scalenia, wysokość = odległość łączenia), a następnie cięty:
scalenie jest wykonane, gdy podobieństwo łączenia >= threshold (inkluzywnie, jak w K01), czyli odległość <= 1 - threshold.
Oba łączenia są monotoniczne (brak inwersji), więc cięcie = zatrzymanie na pierwszym scaleniu poniżej progu.
Remisy rozstrzyga porządek leksykograficzny posortowanych list członków, więc wynik nie zależy od kolejności wejścia.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

from methadata.contracts import (AdapterKind, AdapterSpec, Assignment, AssignmentStatus, ClusterRun, Edge,
                                 Observation, PairEvidence, Relation)

METHOD_VERSION = "1"
DEFAULT_RELATION = Relation.shared_template
LINKAGES = ("average", "complete")
_EPS = 1e-12


@dataclass(frozen=True)
class Excluded:
    material_id: str
    reason: str           # "no_comparisons" | "incomplete_matrix"
    detail: str


@dataclass(frozen=True)
class Merge:
    step: int
    left: tuple[str, ...]
    right: tuple[str, ...]
    similarity: float     # podobieństwo łączenia (średnie lub minimalne po parach między grupami)
    distance: float       # 1 - similarity = wysokość w dendrogramie
    performed: bool       # czy scalenie leży po właściwej stronie cięcia
    edges: tuple[Edge, ...]  # wszystkie pary między grupami (dowody łączenia, z pair_evidence_id)


@dataclass(frozen=True)
class ClusterEvidence:
    cluster_id: str
    members: tuple[str, ...]
    merges: tuple[Merge, ...]            # scalenia, które utworzyły grupę
    merge_similarity: float              # najniższe podobieństwo łączenia wśród scaleń grupy (próg łączenia)
    edges: tuple[Edge, ...]              # wszystkie pary wewnątrz grupy
    weakest: Optional[Edge]


@dataclass(frozen=True)
class LinkageResult:
    run: ClusterRun
    linkage: str
    cut_threshold: float                 # config.threshold (podobieństwo)
    cut_distance: float
    clusters: tuple[ClusterEvidence, ...]
    dendrogram: tuple[Merge, ...]        # pełny dendrogram (także scalenia odrzucone przez cięcie)
    excluded: tuple[Excluded, ...]
    lowest_performed_merge: Optional[float]   # najniższe podobieństwo wykonanego scalenia
    highest_rejected_merge: Optional[float]   # najwyższe podobieństwo odrzuconego scalenia (luka wokół progu)


# ------------------------------------------------------------------ konfiguracja

def resolve_config(config: Mapping[str, Any]) -> dict[str, Any]:
    for key in ("threshold", "similarity_version", "dataset_version"):
        if config.get(key) in (None, ""):
            raise ValueError(f"config.{key} jest wymagane (wejście musi wskazywać wersję cech/podobieństw)")
    thr = config["threshold"]
    if isinstance(thr, bool) or not isinstance(thr, (int, float)) or not (0.0 < thr <= 1.0):
        raise ValueError("config.threshold musi należeć do (0, 1]")
    return {
        "threshold": float(thr),
        "similarity_version": str(config["similarity_version"]),
        "dataset_version": str(config["dataset_version"]),
        "relation": Relation(config.get("relation", DEFAULT_RELATION)).value,
        "method_id": config.get("method_id"),
    }


def _digest(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:12]


def _cluster_id(members: Sequence[str]) -> str:
    return "cl-" + hashlib.sha256("\x1f".join(sorted(members)).encode()).hexdigest()[:12]


def _select_pairs(pairs: Sequence[PairEvidence], cfg: Mapping[str, Any], mats: set[str]):
    """-> {(a,b): (score|None, pair_evidence_id)}; wiele dowodów tej samej pary: wygrywa najwyższy score
    (remis: mniejszy pair_evidence_id), null tylko gdy wszystkie są null."""
    sel = [p for p in pairs if p.relation.value == cfg["relation"]
           and (cfg["method_id"] is None or p.method_id == cfg["method_id"])]
    methods = {p.method_id for p in sel}
    if cfg["method_id"] is None and len(methods) > 1:
        raise ValueError(f"wejście zawiera kilka metod {sorted(methods)}; ustaw config.method_id")
    best: dict[tuple[str, str], tuple[Optional[float], str]] = {}
    for p in sel:
        for c in p.pair:
            if c not in mats:
                raise ValueError(f"PairEvidence {p.pair_evidence_id} wskazuje materiał spoza obserwacji: {c}")
        cand = (p.score, p.pair_evidence_id)
        cur = best.get(p.pair)
        if cur is None:
            best[p.pair] = cand
        elif cand[0] is not None and (cur[0] is None or cand[0] > cur[0] or (cand[0] == cur[0] and cand[1] < cur[1])):
            best[p.pair] = cand
        elif cand[0] is None and cur[0] is None and cand[1] < cur[1]:
            best[p.pair] = cand
    return best


# ------------------------------------------------------------------ macierz

def _comparable_set(mats: Sequence[str], best) -> tuple[list[str], list[Excluded]]:
    scored = {k for k, (s, _) in best.items() if s is not None}
    has = {m for k in scored for m in k}
    excluded = [Excluded(m, "no_comparisons", "brak jakiegokolwiek wykonanego porównania (brak par lub score=null)")
                for m in mats if m not in has]
    keep = [m for m in mats if m in has]
    while True:
        missing = {m: [o for o in keep if o != m and tuple(sorted((m, o))) not in scored] for m in keep}
        worst = max(keep, key=lambda m: (len(missing[m]), [-ord(c) for c in m]), default=None)
        if worst is None or not missing[worst]:
            return keep, excluded
        excluded.append(Excluded(worst, "incomplete_matrix",
                                 f"brak wykonanego porównania z: {', '.join(missing[worst])}"))
        keep.remove(worst)


def _dendrogram(keep: Sequence[str], sim: Mapping[tuple[str, str], float], pid: Mapping[tuple[str, str], str],
                rel: Relation, linkage: str, thr: float) -> list[Merge]:
    def s(a, b):
        return sim[(a, b) if a < b else (b, a)]

    def link(g, h):
        vals = [s(a, b) for a in g for b in h]
        return min(vals) if linkage == "complete" else sum(vals) / len(vals)

    groups: list[tuple[str, ...]] = [(m,) for m in keep]
    out: list[Merge] = []
    while len(groups) > 1:
        best = None
        for i in range(len(groups)):
            for j in range(i + 1, len(groups)):
                v = link(groups[i], groups[j])
                key = (-v, groups[i], groups[j])
                if best is None or key < best[0]:
                    best = (key, i, j, v)
        _, i, j, v = best
        g, h = groups[i], groups[j]
        edges = tuple(Edge(source=a if a < b else b, target=b if a < b else a, relation=rel,
                           pair_evidence_id=pid[(a, b) if a < b else (b, a)], weight=s(a, b))
                      for a in g for b in h)
        edges = tuple(sorted(edges, key=lambda e: (e.source, e.target)))
        out.append(Merge(step=len(out) + 1, left=g, right=h, similarity=v, distance=1.0 - v,
                         performed=v >= thr - _EPS, edges=edges))
        groups = [x for k, x in enumerate(groups) if k not in (i, j)] + [tuple(sorted(g + h))]
        groups.sort()
    return out


# ------------------------------------------------------------------ wynik

def build_result(observations: Sequence[Observation], pair_evidence: Sequence[PairEvidence],
                 config: Mapping[str, Any], *, linkage: str = "average", seed: int = 0) -> LinkageResult:
    if linkage not in LINKAGES:
        raise ValueError(f"linkage musi być jednym z {LINKAGES}")
    cfg = resolve_config(config)
    rel = Relation(cfg["relation"])
    thr = cfg["threshold"]
    mats = sorted({o.canonical_id for o in observations})
    best = _select_pairs(pair_evidence, cfg, set(mats))
    keep, excluded = _comparable_set(mats, best)
    sim = {k: s for k, (s, _) in best.items() if s is not None and k[0] in keep and k[1] in keep}
    pid = {k: p for k, (_, p) in best.items() if k in sim}
    merges = _dendrogram(keep, sim, pid, rel, linkage, thr) if len(keep) > 1 else []

    # cięcie: odtwarzamy grupy z wykonanych scaleń (monotoniczność gwarantuje spójność)
    members = {m: (m,) for m in keep}
    made: dict[tuple[str, ...], list[Merge]] = {(m,): [] for m in keep}
    for mg in merges:
        if not mg.performed:
            continue
        new = tuple(sorted(mg.left + mg.right))
        made[new] = made.pop(mg.left) + made.pop(mg.right) + [mg]
        for m in new:
            members[m] = new

    assignments = [Assignment(material_id=e.material_id, status=AssignmentStatus.insufficient_data) for e in excluded]
    clusters, run_edges = [], []
    for g, ms in sorted(made.items()):
        if len(g) == 1:
            assignments.append(Assignment(material_id=g[0], status=AssignmentStatus.singleton))
            continue
        cid = _cluster_id(g)
        es = tuple(Edge(source=a, target=b, relation=rel, pair_evidence_id=pid[(a, b)], weight=sim[(a, b)])
                   for i, a in enumerate(g) for b in g[i + 1:])
        clusters.append(ClusterEvidence(
            cluster_id=cid, members=g, merges=tuple(ms), merge_similarity=min(m.similarity for m in ms), edges=es,
            weakest=min(es, key=lambda e: (e.weight, e.source, e.target))))
        run_edges += es
        assignments += [Assignment(material_id=m, cluster_ids=[cid], status=AssignmentStatus.grouped) for m in g]
    assignments.sort(key=lambda a: a.material_id)

    run_cfg = {k: v for k, v in cfg.items() if k != "dataset_version"}
    run_cfg["linkage"] = linkage
    run_cfg["cut_distance"] = 1.0 - thr
    method_id = f"linkage-{linkage}"
    run = ClusterRun(
        run_id=f"run-{method_id}-" + _digest([cfg, linkage, seed, mats,
                                             sorted((k, v) for k, v in best.items())]),
        dataset_version=cfg["dataset_version"], method_id=method_id, method_version=METHOD_VERSION,
        config=run_cfg, seed=seed, relation=rel, assignments=assignments, edges=run_edges)
    done = [m.similarity for m in merges if m.performed]
    rej = [m.similarity for m in merges if not m.performed]
    return LinkageResult(run=run, linkage=linkage, cut_threshold=thr, cut_distance=1.0 - thr,
                         clusters=tuple(clusters), dendrogram=tuple(merges), excluded=tuple(excluded),
                         lowest_performed_merge=min(done) if done else None,
                         highest_rejected_merge=max(rej) if rej else None)


def compare_with_components(observations: Sequence[Observation], pair_evidence: Sequence[PairEvidence],
                            config: Mapping[str, Any]) -> dict[str, Any]:
    """Zestawia grupy K01 (single-link: składowe) z average-link i complete-link przy tym samym progu.
    'components_groups_split' = grupy K01 nieobecne w wyniku linkage (rozbicie, np. mostu lub rodziny);
    'merged_beyond_components' = grupy linkage nie mieszczące się w żadnej grupie K01 (nie powinno wystąpić)."""
    from methadata.clustering.components import build_result as comp

    def groups(run):
        g: dict[str, set[str]] = {}
        for a in run.assignments:
            for c in a.cluster_ids:
                g.setdefault(c, set()).add(a.material_id)
        return {frozenset(v) for v in g.values()}

    base = groups(comp(observations, pair_evidence, config).run)
    out: dict[str, Any] = {"components": sorted(sorted(g) for g in base)}
    for lk in LINKAGES:
        res = groups(build_result(observations, pair_evidence, config, linkage=lk).run)
        out[lk] = {
            "groups": sorted(sorted(g) for g in res),
            "components_groups_split": sorted(sorted(g) for g in base if g not in res),
            "merged_beyond_components": sorted(sorted(r) for r in res if not any(r <= g for g in base)),
        }
    return out


# ------------------------------------------------------------------ adaptery

class _Base:
    linkage = ""

    def cluster(self, observations: Sequence[Observation], pair_evidence: Sequence[PairEvidence],
                config: Mapping[str, Any], *, seed: int = 0, optional: Mapping[str, Any] | None = None) -> ClusterRun:
        return build_result(observations, pair_evidence, config, linkage=self.linkage, seed=seed).run


class AverageLinkClusterer(_Base):
    linkage = "average"
    spec = AdapterSpec(id="linkage-average-v1", kind=AdapterKind.clusterer, version=METHOD_VERSION,
                       description="Aglomeracja average-link na 1 - podobieństwo, cięcie na progu")


class CompleteLinkClusterer(_Base):
    linkage = "complete"
    spec = AdapterSpec(id="linkage-complete-v1", kind=AdapterKind.clusterer, version=METHOD_VERSION,
                       description="Aglomeracja complete-link na 1 - podobieństwo, cięcie na progu")


def get_adapters():
    return [AverageLinkClusterer(), CompleteLinkClusterer()]
