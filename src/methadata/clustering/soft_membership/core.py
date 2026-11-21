"""K13: miękkie przypisania (fuzzy c-means albo mieszanina Gaussów) na wektorach cech.

Stopnie przynależności są wynikiem modelu (nie prawdopodobieństwem prawdziwej rodziny). Reguła prezentacji:
materiał trafia do każdego klastra, w którym stopień >= present_threshold; jeśli najwyższy stopień
< min_membership, jest szumem (noise). Zbyt mała próba daje jawny status insufficient_data.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from itertools import combinations
from typing import Any, Mapping, Sequence

from methadata.contracts import (
    AdapterKind, AdapterSpec, Assignment, AssignmentStatus, ClusterRun, Observation, Relation,
)

METHOD_VERSION = "1"
PRESENTATION_RULE = ("Materiał należy do każdego klastra ze stopniem >= present_threshold (może być kilka: materiał "
                     "graniczny); gdy max stopień < min_membership -> noise. Stopnie to wynik modelu, nie pewność.")
ALGORITHMS = ("fcm", "gmm")


@dataclass(frozen=True)
class MembershipEvidence:
    material_id: str
    reason: str  # member | border | low_membership | too_few_samples | no_vector
    memberships: tuple[tuple[str, float], ...] = ()  # (cluster_id, stopień), malejąco; tylko klastry niepuste
    top_cluster_id: str | None = None
    margin: float | None = None  # różnica 1. i 2. stopnia (małe = graniczny)
    entropy: float | None = None  # znormalizowana do [0, 1]
    presented_cluster_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class SoftResult:
    run: ClusterRun
    evidence: tuple[MembershipEvidence, ...]
    memberships: dict[str, dict[str, float]]  # materiał -> klaster -> stopień
    status_note: str  # ok | too_few_samples | no_vectors
    objective: float | None
    presentation_rule: str = PRESENTATION_RULE


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:16]


def _numpy():
    try:
        import numpy as np
    except ImportError as exc:
        raise ImportError("K13 wymaga dodatku density. Zainstaluj: pip install '.[density]'") from exc
    return np


def _params(config: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in ("dataset_version", "vector_version"):
        value = config.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"config.{key} jest wymagane")
        out[key] = value
    algorithm = config.get("algorithm", "fcm")
    if algorithm not in ALGORITHMS:
        raise ValueError(f"config.algorithm musi być jednym z {ALGORITHMS}")
    c = config.get("components", 2)
    if isinstance(c, bool) or not isinstance(c, int) or c < 1:
        raise ValueError("config.components musi być liczbą całkowitą >= 1")
    mpc = config.get("min_samples_per_component", 3)
    if isinstance(mpc, bool) or not isinstance(mpc, int) or mpc < 1:
        raise ValueError("config.min_samples_per_component musi być liczbą całkowitą >= 1")
    fuzz = config.get("fuzzifier", 2.0)
    if isinstance(fuzz, bool) or not isinstance(fuzz, (int, float)) or fuzz <= 1:
        raise ValueError("config.fuzzifier musi być > 1")
    for key, default in (("present_threshold", 0.3), ("min_membership", 0.5)):
        v = config.get(key, default)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 < v <= 1:
            raise ValueError(f"config.{key} poza zakresem (0, 1]")
        out[key] = float(v)
    pca = config.get("pca_dims")
    if pca is not None and (isinstance(pca, bool) or not isinstance(pca, int) or pca < 1):
        raise ValueError("config.pca_dims musi być liczbą całkowitą >= 1 lub None")
    out.update(algorithm=algorithm, components=c, min_samples_per_component=mpc, fuzzifier=float(fuzz),
               pca_dims=pca, relation=Relation(config.get("relation", Relation.shared_template)).value,
               method_id=config.get("method_id"))
    return out


def _fcm(x, c: int, m: float, seed: int, n_init: int = 5, iters: int = 300, tol: float = 1e-8):
    """Fuzzy c-means; zwraca (U[n,c], centra, funkcja celu). Najlepszy z n_init startów (losowe U z seedu)."""
    np = _numpy()
    n = len(x)
    best = None
    for r in range(n_init):
        rng = np.random.RandomState(seed + 7919 * r)
        u = rng.rand(n, c) + 1e-3
        u /= u.sum(axis=1, keepdims=True)
        for _ in range(iters):
            w = u ** m
            centers = (w.T @ x) / w.sum(axis=0)[:, None]
            d2 = ((x[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2)
            d2 = np.maximum(d2, 1e-12)
            inv = d2 ** (-1.0 / (m - 1.0))
            new = inv / inv.sum(axis=1, keepdims=True)
            done = np.abs(new - u).max() < tol
            u = new
            if done:
                break
        w = u ** m
        centers = (w.T @ x) / w.sum(axis=0)[:, None]
        d2 = ((x[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2)
        obj = float((w * d2).sum())
        if best is None or obj < best[2] - 1e-12:
            best = (u, centers, obj)
    return best


def _gmm(x, c: int, seed: int):
    try:
        from sklearn.mixture import GaussianMixture
    except ImportError as exc:
        raise ImportError("K13 wymaga dodatku density. Zainstaluj: pip install '.[density]'") from exc
    gm = GaussianMixture(n_components=c, covariance_type="diag", reg_covar=1e-4, n_init=5, random_state=seed).fit(x)
    return gm.predict_proba(x), gm.means_, float(-gm.score(x) * len(x))


def _prepare(vectors, comparable, pca_dims):
    np = _numpy()
    x = np.array([vectors[m] for m in comparable], dtype=float)
    norms = np.linalg.norm(x, axis=1, keepdims=True)
    if not np.all(norms > 0):
        raise ValueError("wektor zerowy nie podlega normalizacji")
    x = x / norms
    if pca_dims and pca_dims < min(x.shape):
        try:
            from sklearn.decomposition import PCA
        except ImportError as exc:
            raise ImportError("K13 wymaga dodatku density. Zainstaluj: pip install '.[density]'") from exc
        x = PCA(n_components=pca_dims, random_state=0).fit_transform(x)
    return x


def fit_memberships(x, cfg: Mapping[str, Any], seed: int):
    """Macierz stopni przynależności [n, c] (wiersze sumują się do 1), środki i funkcja celu."""
    c = min(cfg["components"], len(x))
    if cfg["algorithm"] == "gmm":
        return _gmm(x, c, seed)
    return _fcm(x, c, cfg["fuzzifier"], seed)


def build_result(observations: Sequence[Observation], vectors: Mapping[str, Sequence[float]],
                 config: Mapping[str, Any], *, seed: int = 0) -> SoftResult:
    cfg = _params(config)
    np = _numpy()
    materials = sorted({o.canonical_id for o in observations})
    unknown = set(vectors) - set(materials)
    if unknown:
        raise ValueError(f"wektory dla materiałów spoza obserwacji: {sorted(unknown)}")
    comparable = [m for m in materials if m in vectors]
    status = {m: Assignment(material_id=m, status=AssignmentStatus.insufficient_data) for m in materials}
    evidence = {m: MembershipEvidence(m, "no_vector") for m in materials if m not in vectors}
    memberships: dict[str, dict[str, float]] = {}
    objective, note = None, "ok"
    required = max(cfg["components"] + 1, cfg["min_samples_per_component"] * cfg["components"])
    if not comparable:
        note = "no_vectors"
    elif len(comparable) < required:
        note = "too_few_samples"
        for m in comparable:
            evidence[m] = MembershipEvidence(m, "too_few_samples")
    else:
        x = _prepare(vectors, comparable, cfg["pca_dims"])
        u, _, objective = fit_memberships(x, cfg, seed)
        u = np.asarray(u, dtype=float)
        core = u.argmax(axis=1)
        cid = {}
        for j in sorted(set(int(v) for v in core)):
            cid[j] = "cl-" + _digest(sorted(comparable[i] for i in range(len(comparable)) if core[i] == j))
        c = u.shape[1]
        for i, m in enumerate(comparable):
            row = sorted(((cid[j], float(u[i, j])) for j in cid), key=lambda t: (-t[1], t[0]))
            memberships[m] = dict(row)
            top = row[0][1]
            margin = top - (row[1][1] if len(row) > 1 else 0.0)
            p = u[i][u[i] > 0]
            ent = float(-(p * np.log(p)).sum() / math.log(c)) if c > 1 else 0.0
            shown = tuple(k for k, v in row if v >= cfg["present_threshold"])
            if top < cfg["min_membership"] and len(shown) < 2 or not shown:
                status[m] = Assignment(material_id=m, status=AssignmentStatus.noise)
                reason, shown = "low_membership", ()
            else:
                status[m] = Assignment(material_id=m, cluster_ids=sorted(shown), status=AssignmentStatus.grouped)
                reason = "border" if len(shown) > 1 else "member"
            evidence[m] = MembershipEvidence(m, reason, tuple(row), row[0][0], margin, ent, shown)
    cfg_out = {k: v for k, v in cfg.items() if k != "dataset_version"}
    cfg_out.update(presentation_rule=PRESENTATION_RULE, status_note=note, required_samples=required,
                   n_vectors=len(comparable), measure="degree_of_membership_model_output")
    method_id = cfg["method_id"] or f"soft-{cfg['algorithm']}"
    run = ClusterRun(
        run_id=f"run-{method_id}-" + _digest([cfg_out, seed, materials,
                                              [(m, _digest(list(vectors[m]))) for m in comparable]]),
        dataset_version=cfg["dataset_version"], method_id=method_id, method_version=METHOD_VERSION,
        config=cfg_out, seed=seed, relation=Relation(cfg["relation"]),
        assignments=[status[m] for m in materials], edges=[],
    )
    return SoftResult(run, tuple(evidence[m] for m in materials), memberships, note, objective)


def _hard(memberships: Mapping[str, Mapping[str, float]]) -> dict[str, str]:
    return {m: max(row, key=lambda k: (row[k], k)) for m, row in memberships.items()}


def pair_agreement(a: Mapping[str, str], b: Mapping[str, str]) -> float | None:
    """Indeks Randa na wspólnych materiałach (niezależny od nazw klastrów)."""
    common = sorted(set(a) & set(b))
    if len(common) < 2:
        return None
    pairs = list(combinations(common, 2))
    return sum((a[x] == a[y]) == (b[x] == b[y]) for x, y in pairs) / len(pairs)


def stability(observations: Sequence[Observation], vectors: Mapping[str, Sequence[float]], config: Mapping[str, Any],
              *, components: Sequence[int] = (), fractions: Sequence[float] = (1.0,), repeats: int = 3,
              seed: int = 0) -> list[dict[str, Any]]:
    """Zgodność twardych etykiet (argmax) z przebiegiem odniesienia (pełny zbiór, config.components)
    dla zmienianej liczby składników i rozmiaru zbioru (podpróby bez zwracania, `repeats` losowań)."""
    np = _numpy()
    ref = build_result(observations, vectors, config, seed=seed)
    ref_hard = _hard(ref.memberships)
    ids = sorted(vectors)
    rows = []
    for c in components or [config.get("components", 2)]:
        for frac in fractions:
            agree, statuses = [], set()
            for r in range(repeats if frac < 1 else 1):
                rng = np.random.RandomState(seed + 31 * r + 1)
                size = max(1, int(round(frac * len(ids))))
                pick = sorted(rng.choice(len(ids), size=size, replace=False)) if frac < 1 else range(len(ids))
                sub = {ids[i]: vectors[ids[i]] for i in pick}
                res = build_result(observations, sub, {**config, "components": c}, seed=seed + r)
                statuses.add(res.status_note)
                if res.status_note == "ok":
                    a = pair_agreement(ref_hard, _hard(res.memberships))
                    if a is not None:
                        agree.append(a)
            rows.append({"components": c, "fraction": frac, "n": len(ids) if frac >= 1 else size,
                         "status": sorted(statuses), "pair_agreement_mean": float(np.mean(agree)) if agree else None,
                         "pair_agreement_min": float(min(agree)) if agree else None})
    return rows


class SoftMembershipClusterer:
    spec = AdapterSpec(id="soft-membership-v1", kind=AdapterKind.clusterer, version=METHOD_VERSION,
                       description="Miękkie przypisania (fuzzy c-means / GMM) na wektorach cech")

    def cluster(self, observations: Sequence[Observation], pair_evidence: Sequence[Any], config: Mapping[str, Any],
                *, seed: int = 0, optional: Mapping[str, Any] | None = None) -> ClusterRun:
        vectors = config.get("vectors") or (optional or {}).get("vectors")
        if not vectors:
            raise ValueError("soft-membership-v1 wymaga wektorów (config.vectors lub optional['vectors'])")
        cfg = {k: v for k, v in config.items() if k != "vectors"}
        return build_result(observations, vectors, cfg, seed=seed).run


def get_adapters() -> list[SoftMembershipClusterer]:
    return [SoftMembershipClusterer()]
