"""PhyloFake v1 — Adversary-Cost Information Gain (ACIG), Slot-Swap Mutation Detection,
Directed Campaign Phylogeny ("Patient Zero") & 2-Hop Hypergraph Diffusion.

Działa w 100% deterministycznie na cechach i dowodach z istniejących ekstraktorów Methadata (bez nowych zależności).
"""
from __future__ import annotations

import math
from collections import defaultdict, deque
from typing import Any, Mapping, TypedDict

# Koszt obejścia śladu przez operatora kampanii (C_mutate)
ADVERSARY_COST: dict[str, float] = {
    "tracker": 2.4,         # Meta Pixel / Google Ads ID — najdroższe / najrzadziej zmieniane
    "contact": 2.2,         # Telefon / e-mail / Telegram
    "affiliate": 2.1,       # Kod afiliacyjny
    "script": 2.0,          # Własny skrypt JS / lejek konwersji
    "voice": 2.0,           # SpeechBrain ECAPA-TDNN / lektor / syntezator
    "infrastructure": 1.9,  # Wspólne IP hostingu / rejestrator
    "dom": 1.7,             # Szkielet DOM / szablon strony
    "image": 1.2,           # Wspólne grafiki / pHash
    "domain": 0.9,          # Domena (często rotowana)
    "brand": 0.7,           # Marka / produkt
    "person": 0.6,          # Osoba publiczna (często podmieniana w fabryce szablonów)
}

MUTATION_LABELS: dict[str, str] = {
    "CLONE": "Klon kampanii",
    "CELEBRITY_SWAP": "Podmiana osoby",
    "PRODUCT_SWAP": "Podmiana produktu",
    "CROSS_MEDIA_BRIDGE": "Most wideo/audio",
    "INFRA_REUSE": "Wspólna infrastruktura",
    "TEMPLATE_VARIANT": "Wariant szablonu",
}

MUTATION_SHORT: dict[str, str] = {
    "CLONE": "KLON",
    "CELEBRITY_SWAP": "SWAP OSOBY",
    "PRODUCT_SWAP": "SWAP PRODUKTU",
    "CROSS_MEDIA_BRIDGE": "AUDIO/WIDEO",
    "INFRA_REUSE": "INFRA",
    "TEMPLATE_VARIANT": "WARIANT",
}


class PhyloFakeResult(TypedDict):
    version: str
    edges: dict[str, dict[str, Any]]
    clusters: dict[str, dict[str, Any]]
    bridges: list[dict[str, Any]]


def _idf(n_materials: int, df: int) -> float:
    return math.log((n_materials + 1.0) / (df + 1.0)) + 0.15


_KNOWN_PERSONS: tuple[tuple[str, str], ...] = (
    ("brzosk", "Rafał Brzoska"),
    ("stanowsk", "Krzysztof Stanowski"),
    ("sekielsk", "Tomasz Sekielski"),
    ("morawieck", "Mateusz Morawiecki"),
    ("olejnik", "Monika Olejnik"),
    ("glapińsk", "Adam Glapiński"),
)

_KNOWN_PRODUCTS: tuple[tuple[str, str], ...] = (
    ("kardivix", "Kardivix"),
    ("relieflex", "RelieFlex"),
    ("inpost", "InPost"),
)


def _extract_entities_by_material(
    bundle, artifacts: list[dict[str, Any]], search_text: Mapping[str, str]
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Zbiera osoby publiczne i marki/produkty przypisane do każdego materiału (z cech, artefaktów i indeksu treści)."""
    persons: dict[str, set[str]] = defaultdict(set)
    brands: dict[str, set[str]] = defaultdict(set)
    if bundle is not None:
        obs_to_canon = {o.observation_id: o.canonical_id for o in bundle.observations}
        for feat in bundle.features:
            if feat.family != "entity" or not isinstance(feat.value, Mapping):
                continue
            mid = obs_to_canon.get(feat.observation_id)
            name = str(feat.value.get("canonical") or feat.value.get("name") or "").strip()
            if not mid or not name:
                continue
            if "person" in feat.kind:
                persons[mid].add(name)
            else:
                brands[mid].add(name)
    for art in artifacts:
        kind = art.get("kind")
        label = str(art.get("label") or "").replace(" (wizualnie)", "").strip()
        if not label:
            continue
        for mid in art.get("material_ids", ()):
            if kind == "person":
                persons[mid].add(label)
            elif kind == "brand":
                brands[mid].add(label)
    for mid, text in search_text.items():
        low = text.lower()
        for stem, canonical in _KNOWN_PERSONS:
            if stem in low:
                persons[mid].add(canonical)
        for stem, canonical in _KNOWN_PRODUCTS:
            if stem in low:
                brands[mid].add(canonical)
    return persons, brands


def _media_kinds_by_material(bundle) -> dict[str, set[str]]:
    out: dict[str, set[str]] = defaultdict(set)
    if bundle is None:
        return out
    for obs in bundle.observations:
        for art in obs.artifacts:
            role = art.role.value
            if role in {"video", "audio"}:
                out[obs.canonical_id].add("av")
            elif role in {"landing_html", "landing_dom"}:
                out[obs.canonical_id].add("web")
            elif role in {"creative_image", "landing_screenshot"}:
                out[obs.canonical_id].add("image")
    return out


def _pair_family_scores(bundle) -> dict[tuple[str, str], dict[str, float]]:
    out: dict[tuple[str, str], dict[str, float]] = defaultdict(dict)
    if bundle is None:
        return out
    for p in bundle.pairs:
        if p.score is None:
            continue
        pkey = tuple(sorted(p.pair))
        out[pkey][p.method_id] = max(out[pkey].get(p.method_id, 0.0), float(p.score))
    return out


def _earliest_ts(mid: str, captured_at: Mapping[str, str], domains: Mapping[str, list[str]],
                 domain_meta: Mapping[str, Mapping[str, Any]]) -> tuple[str, str]:
    """Zwraca (iso_timestamp, źródło_daty) dla materiału (najwcześniejsza z: zapis materiału, RDAP created, TLS)."""
    candidates: list[tuple[str, str]] = []
    if captured_at.get(mid):
        candidates.append((str(captured_at[mid]), "zapis materiału"))
    for host in domains.get(mid, ()):
        meta = domain_meta.get(host) or {}
        if meta.get("created"):
            candidates.append((str(meta["created"]), f"rejestracja domeny {host}"))
    if not candidates:
        return "", ""
    candidates.sort(key=lambda item: item[0])
    return candidates[0]


def analyze_phylofake(bundle, graph: Mapping[str, Any]) -> PhyloFakeResult:
    """Uruchamia pełną analizę PhyloFake v1 na bieżącej migawce grafu i bundlu cech."""
    materials = [m["material_id"] for m in graph.get("materials", ())]
    material_labels = {m["material_id"]: m["label"] for m in graph.get("materials", ())}
    n_mat = max(1, len(materials))
    inv = graph.get("investigation") or {}
    artifacts = list(inv.get("artifacts") or [])
    domains = inv.get("domains") or {}
    domain_meta = inv.get("domain_meta") or {}
    captured_at = inv.get("captured_at") or {}
    edge_families = inv.get("edge_families") or {}

    persons, brands = _extract_entities_by_material(bundle, artifacts, inv.get("search_text") or {})
    media_kinds = _media_kinds_by_material(bundle)
    raw_scores = _pair_family_scores(bundle)

    # 1. Zbuduj ważony hipergraf Materiał <-> Ślad (wagi ACIG = IDF * C_mutate)
    trace_weights: dict[tuple[str, str], float] = {}
    mat_traces: dict[str, list[tuple[tuple[str, str], float]]] = defaultdict(list)
    trace_mats: dict[tuple[str, str], list[str]] = {}
    for art in artifacts:
        kind = str(art.get("kind") or "domain")
        label = str(art.get("label") or "")
        mids = [m for m in art.get("material_ids", ()) if m in material_labels]
        if len(mids) < 2:
            continue
        cost = ADVERSARY_COST.get(kind, 1.0)
        w = round(_idf(n_mat, len(mids)) * cost, 4)
        tkey = (kind, label)
        trace_weights[tkey] = w
        trace_mats[tkey] = mids
        for mid in mids:
            mat_traces[mid].append((tkey, w))

    # Zbierz wszystkie krawędzie (potwierdzone + sugerowane) do otagowania mutacji i ACIG
    all_edges: list[tuple[str, str, str, float, bool]] = []
    for e in graph.get("cluster_run", {}).get("edges") or ():
        all_edges.append((e["pair_evidence_id"], e["source"], e["target"], float(e.get("weight") or 0.0), False))
    for se in inv.get("suggested_edges") or ():
        all_edges.append((se["pair_evidence_id"], se["source"], se["target"], float(se.get("score") or 0.0), True))

    edge_results: dict[str, dict[str, Any]] = {}
    max_raw_acig = 1.0

    # Najpierw oblicz surowe wagi ACIG dla krawędzi, aby znormalizować do [0, 1]
    raw_edge_acig: dict[str, tuple[float, list[tuple[tuple[str, str], float]]]] = {}
    for eid, src, dst, score, _is_sug in all_edges:
        traces_a = dict(mat_traces.get(src, ()))
        shared = [(tk, w) for tk, w in mat_traces.get(dst, ()) if tk in traces_a]
        shared.sort(key=lambda item: -item[1])
        total_w = sum(w for _, w in shared)
        # Uwzględnij także wysoki koszt DOM / Voice z raw_scores, nawet gdy nie ma jawnego artefaktu
        pkey = tuple(sorted((src, dst)))
        ps = raw_scores.get(pkey, {})
        dom_sc = max((v for k, v in ps.items() if "dom" in k), default=0.0)
        voice_sc = ps.get("voice-aux-v1", 0.0)
        if dom_sc >= 0.6:
            total_w += dom_sc * ADVERSARY_COST["dom"] * 0.6
        if voice_sc >= 0.65:
            total_w += voice_sc * ADVERSARY_COST["voice"] * 0.6
        raw_edge_acig[eid] = (total_w, shared)
        if total_w > max_raw_acig:
            max_raw_acig = total_w

    # 2. Klasyfikacja mutacji (Slot-Swap) dla każdej krawędzi
    for eid, src, dst, score, _is_sug in all_edges:
        pkey = tuple(sorted((src, dst)))
        ps = raw_scores.get(pkey, {})
        fams = set(edge_families.get(eid, ()))
        dom_sc = max((v for k, v in ps.items() if "dom" in k), default=0.0)
        text_sc = max((v for k, v in ps.items() if any(t in k for t in ("lexical", "near-copy", "semantic", "text"))), default=0.0)
        asset_sc = max((v for k, v in ps.items() if any(t in k for t in ("asset", "phash", "visual"))), default=0.0)
        av_sc = max((ps.get(k, 0.0) for k in ("video-frames-v1", "audio-fingerprint-v1", "transcript-ngram-v1", "voice-aux-v1")), default=0.0)

        pa, pb = persons.get(src, set()), persons.get(dst, set())
        ba, bb = brands.get(src, set()), brands.get(dst, set())
        ka, kb = media_kinds.get(src, set()), media_kinds.get(dst, set())

        total_w, shared = raw_edge_acig[eid]
        # Połącz znormalizowany ACIG z bazowym score
        acig_norm = round(min(1.0, 0.65 * (1.0 - math.exp(-total_w / max(2.2, max_raw_acig * 0.45))) + 0.35 * score), 3)

        if av_sc >= 0.4 or ("av" in ka and "av" not in kb) or ("av" in kb and "av" not in ka):
            mut = "CROSS_MEDIA_BRIDGE"
            detail = "Powiązanie między materiałem audio/wideo a innymi zasobami (ścieżka dźwiękowa, transkrypcja lub głos SpeechBrain)."
        elif (dom_sc >= 0.55 or "dom" in fams or any(k in {"script", "tracker"} for (k, _), _ in shared)) and pa and pb and not (pa & pb):
            mut = "CELEBRITY_SWAP"
            detail = f"Ten sam szablon lub zaplecze, podmieniona osoba publiczna: {sorted(pa)[0]} ↔ {sorted(pb)[0]}."
        elif (dom_sc >= 0.55 or "dom" in fams) and ba and bb and not (ba & bb):
            mut = "PRODUCT_SWAP"
            detail = f"Ten sam szablon strony, podmieniony produkt lub marka: {sorted(ba)[0]} ↔ {sorted(bb)[0]}."
        elif (score >= 0.85 or (dom_sc >= 0.7 and text_sc >= 0.75)) and (asset_sc >= 0.5 or "asset" in fams or not (pa ^ pb)):
            mut = "CLONE"
            detail = "Bezpośredni klon tej samej kampanii (zgodny szablon, treść i kreacje — prawdopodobna rotacja domeny)."
        elif any(k in {"tracker", "infrastructure", "script", "contact", "affiliate"} for (k, _), _ in shared):
            mut = "INFRA_REUSE"
            top_k, top_lbl = shared[0][0]
            detail = f"Wspólne zaplecze techniczne operatora ({top_k}: {top_lbl[:48]})."
        else:
            mut = "TEMPLATE_VARIANT"
            detail = "Wariant tego samego szablonu lub schematu narracji."

        top_traces = [
            f"{k}: {lbl[:38]}{'…' if len(lbl) > 38 else ''}"
            for (k, lbl), _w in shared[:4]
        ]
        edge_results[eid] = {
            "mutation": mut,
            "mutation_label": MUTATION_LABELS[mut],
            "mutation_short": MUTATION_SHORT[mut],
            "detail": detail,
            "acig": acig_norm,
            "top_traces": top_traces,
        }

    # 3. Skierowana filogeneza kampanii ("Patient Zero" + drzewo ewolucji w każdym klastrze)
    accepted_edges = graph.get("cluster_run", {}).get("edges") or []
    cluster_results: dict[str, dict[str, Any]] = {}
    for cluster in graph.get("clusters") or ():
        cid = cluster["cluster_id"]
        members = list(cluster["material_ids"])
        mset = set(members)
        c_edges = [e for e in accepted_edges if e["source"] in mset and e["target"] in mset]
        deg: dict[str, int] = defaultdict(int)
        for e in c_edges:
            deg[e["source"]] += 1
            deg[e["target"]] += 1

        # Wybierz Patient Zero: najpierw najwcześniejszy timestamp, potem najwyższy stopień, potem liczba śladów
        scored_members = []
        for m in members:
            ts, ts_src = _earliest_ts(m, captured_at, domains, domain_meta)
            has_ts = 0 if ts else 1
            scored_members.append((has_ts, ts or "9999", -deg[m], -len(mat_traces.get(m, ())), m, ts_src))
        scored_members.sort()
        pz_ts_flag, pz_ts, _neg_deg, _neg_tr, pz_id, pz_src = scored_members[0]
        if pz_ts_flag == 0:
            pz_reason = f"Najwcześniejszy ślad czasowy ({pz_ts[:10]}, {pz_src}) · {deg[pz_id]} powiązań"
        else:
            pz_reason = f"Centralny węzeł grupy ({deg[pz_id]} powiązań, {len(mat_traces.get(pz_id, ()))} wspólnych śladów)"

        # Zbuduj skierowane drzewo ewolucji od Patient Zero (BFS po najsilniejszych krawędziach)
        adj: dict[str, list[tuple[float, str, str]]] = defaultdict(list)
        for e in c_edges:
            eid = e["pair_evidence_id"]
            acig = edge_results.get(eid, {}).get("acig", float(e.get("weight") or 0.0))
            adj[e["source"]].append((acig, e["target"], eid))
            adj[e["target"]].append((acig, e["source"], eid))
        for m in adj:
            adj[m].sort(key=lambda item: -item[0])

        visited = {pz_id}
        q: deque[str] = deque([pz_id])
        tree: list[dict[str, Any]] = []
        while q:
            cur = q.popleft()
            for acig, nxt, eid in adj.get(cur, ()):
                if nxt in visited:
                    continue
                visited.add(nxt)
                q.append(nxt)
                er = edge_results.get(eid, {})
                tree.append({
                    "from_id": cur,
                    "to_id": nxt,
                    "evidence_id": eid,
                    "mutation": er.get("mutation", "TEMPLATE_VARIANT"),
                    "mutation_label": er.get("mutation_label", "Wariant"),
                    "acig": acig,
                })

        mut_counts: dict[str, int] = defaultdict(int)
        acig_vals = []
        for e in c_edges:
            er = edge_results.get(e["pair_evidence_id"])
            if er:
                mut_counts[er["mutation"]] += 1
                acig_vals.append(er["acig"])
        dominant = max(mut_counts.items(), key=lambda kv: kv[1])[0] if mut_counts else "TEMPLATE_VARIANT"
        cluster_results[cid] = {
            "patient_zero": pz_id,
            "patient_zero_label": material_labels.get(pz_id, pz_id),
            "patient_zero_reason": pz_reason,
            "dominant_mutation": dominant,
            "dominant_mutation_label": MUTATION_LABELS[dominant],
            "avg_acig": round(sum(acig_vals) / len(acig_vals), 3) if acig_vals else 0.0,
            "tree": tree,
        }

    # 4. 2-skokowa dyfuzja na hipergrafie (szukanie ukrytych mostów między niepołączonymi materiałami)
    direct_pairs = {tuple(sorted((e["source"], e["target"]))) for e in accepted_edges}
    bridge_scores: dict[tuple[str, str], tuple[float, str, str]] = {}
    for mid_b in materials:
        # Sąsiedzi mid_b w hipergrafie o najwyższym ACIG
        nbr_w: dict[str, tuple[float, str]] = {}
        for tkey, w in mat_traces.get(mid_b, ()):
            if w < 1.2:
                continue
            for other in trace_mats.get(tkey, ()):
                if other == mid_b:
                    continue
                prev = nbr_w.get(other)
                if prev is None or w > prev[0]:
                    nbr_w[other] = (w, f"{tkey[0]}: {tkey[1][:32]}")
        top_nbrs = sorted(nbr_w.items(), key=lambda kv: -kv[1][0])[:8]
        for i in range(len(top_nbrs)):
            a, (wa, ta) = top_nbrs[i]
            for j in range(i + 1, len(top_nbrs)):
                c_id, (wc, tc) = top_nbrs[j]
                pkey = tuple(sorted((a, c_id)))
                if pkey in direct_pairs:
                    continue
                score = round(min(0.99, (wa * wc) / (wa + wc + 3.0)), 3)
                prev = bridge_scores.get(pkey)
                if prev is None or score > prev[0]:
                    path = f"{material_labels.get(a, a)} ──[{ta}]──▶ {material_labels.get(mid_b, mid_b)} ──[{tc}]──▶ {material_labels.get(c_id, c_id)}"
                    bridge_scores[pkey] = (score, mid_b, path)

    bridges = [
        {
            "source": pkey[0],
            "target": pkey[1],
            "via": via,
            "score": sc,
            "path": path,
        }
        for pkey, (sc, via, path) in sorted(bridge_scores.items(), key=lambda kv: -kv[1][0])[:10]
        if sc >= 0.25
    ]

    return {
        "version": "phylofake-v1",
        "edges": edge_results,
        "clusters": cluster_results,
        "bridges": bridges,
    }
