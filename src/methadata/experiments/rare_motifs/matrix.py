"""Macierz materiał–ślad i równoważny graf dwudzielny; częstości składników i koniunkcji z tego samego zbioru."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any, Iterable, Mapping, Optional, Sequence

from .traces import Hit, TraceConfig, extract_traces

SCHEMA = "methadata.rare_motifs.matrix/1"


def _ref_dict(r) -> dict:
    return r.model_dump(mode="json", exclude_none=True)


@dataclass
class TraceMatrix:
    cfg: TraceConfig
    dataset_version: str = ""
    rows: dict[str, dict[str, Hit]] = field(default_factory=dict)          # canonical_id -> trace_id -> Hit
    traces: dict[str, tuple[str, str]] = field(default_factory=dict)        # trace_id -> (kind, label)
    _post: Optional[dict[str, frozenset]] = field(default=None, repr=False)

    @classmethod
    def build(cls, observations: Sequence[Any], feature_map: Mapping[str, Sequence[Any]], cfg: TraceConfig = TraceConfig(),
              dataset_version: str = "") -> "TraceMatrix":
        """Jeden materiał (canonical_id) = jeden wiersz; przy duplikatach wygrywa najmniejszy observation_id (jak runner)."""
        m = cls(cfg=cfg, dataset_version=dataset_version)
        chosen: dict[str, Any] = {}
        for o in sorted(observations, key=lambda o: o.observation_id):
            chosen.setdefault(o.canonical_id, o)
        for cid in sorted(chosen):
            o = chosen[cid]
            row = {}
            for tid, (kind, label, hit) in extract_traces(o.observation_id, feature_map.get(o.observation_id, ()), cfg).items():
                row[tid] = hit
                m.traces[tid] = (kind, label)
            m.rows[cid] = row
        return m

    # ------------------------------------------------------------ częstości
    @property
    def n(self) -> int:
        return len(self.rows)

    def postings(self) -> dict[str, frozenset]:
        if self._post is None:
            p: dict[str, set] = {}
            for cid, row in self.rows.items():
                for t in row:
                    p.setdefault(t, set()).add(cid)
            self._post = {t: frozenset(s) for t, s in p.items()}
        return self._post

    def df(self, trace_id: str) -> int:
        return len(self.postings().get(trace_id, ()))

    def joint_df(self, trace_ids: Iterable[str]) -> int:
        return len(self.joint_set(trace_ids))

    def joint_set(self, trace_ids: Iterable[str]) -> frozenset:
        p = self.postings()
        sets = [p.get(t, frozenset()) for t in trace_ids]
        if not sets:
            return frozenset(self.rows)
        sets.sort(key=len)
        out = sets[0]
        for s in sets[1:]:
            out = out & s
        return out

    def idf(self, trace_id: str) -> float:
        """ln(N / df); ślad nieobecny w korpusie = ślad pojedynczy (df=1)."""
        return math.log(self.n / max(1, self.df(trace_id))) if self.n else 0.0

    def combination_stats(self, trace_ids: Sequence[str]) -> dict:
        """Waga koniunkcji względem częstości składników: Σidf, support (df koniunkcji), oczekiwany support przy
        niezależności N·Π(df/N) oraz lift = support / oczekiwany."""
        n = self.n
        sup = self.joint_df(trace_ids)
        exp = n * math.prod(self.df(t) / n for t in trace_ids) if n else 0.0
        return {"weight": sum(self.idf(t) for t in trace_ids), "support": sup,
                "component_df": {t: self.df(t) for t in trace_ids},
                "expected_support": exp, "lift": (sup / exp) if exp > 0 else None}

    # ------------------------------------------------------------ eksport
    def export(self, *, min_df: int = 1, with_conjunctions: Optional[Sequence[Any]] = None) -> dict:
        """Macierz rzadka + graf dwudzielny (te same krawędzie) z identyfikatorami źródłowymi.
        min_df: pomiń kolumny o df < min_df (np. 2 = ślady jednorazowe nie niosą powtórzenia)."""
        post = self.postings()
        cols = sorted(t for t in self.traces if len(post.get(t, ())) >= min_df)
        colset = set(cols)
        rows = sorted(self.rows)
        ri = {c: i for i, c in enumerate(rows)}
        ci = {t: i for i, t in enumerate(cols)}
        entries, edges = [], []
        for cid in rows:
            for tid in sorted(self.rows[cid]):
                if tid not in colset:
                    continue
                h = self.rows[cid][tid]
                entries.append([ri[cid], ci[tid]])
                edges.append({"material": cid, "trace": tid, "observation_id": h.observation_id,
                              "feature_ids": h.feature_ids, "evidence_refs": [_ref_dict(r) for r in h.refs]})
        tinfo = [{"trace_id": t, "kind": self.traces[t][0], "label": self.traces[t][1], "df": len(post[t]),
                  "idf": self.idf(t)} for t in cols]
        out = {"schema": SCHEMA, "dataset_version": self.dataset_version, "config": self.cfg.to_dict(),
               "n_materials": self.n,
               "matrix": {"rows": rows, "columns": cols, "entries": entries, "shape": [len(rows), len(cols)]},
               "traces": tinfo,
               "graph": {"nodes": [{"id": c, "part": "material"} for c in rows]
                                  + [{"id": t["trace_id"], "part": "trace", "kind": t["kind"]} for t in tinfo],
                         "edges": edges}}
        if with_conjunctions is not None:
            out["conjunctions"] = [m.to_dict() for m in with_conjunctions]
        return out


def dumps(export: dict) -> bytes:
    return json.dumps(export, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def store_export(export: dict, store: Any, *, observation_id: str, parent, origin: str = "rare-motifs-export"):
    """Zapis wyłącznie przez kanoniczny DerivedStore (F02); role=graph_edges czytają K08/K15."""
    from methadata.contracts.models import ArtifactRole, ParentRelation
    return store.put_derived(observation_id=observation_id, role=ArtifactRole.graph_edges, data=dumps(export),
                             media_type="application/json", parent=parent,
                             parent_relation=ParentRelation.derived_from, origin=origin)
