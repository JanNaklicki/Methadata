"""F06: ocena fuzji (K09) na rzeczywistym korpusie. Ten sam zbiór cech i porównań (zasoby E01, tekst E02, DOM E03)
jest fuzowany w wielu profilach: baza (same zasoby), pojedyncze rodziny, szablon, kampania, kampania ścisła oraz
wyłączenie po jednej rodzinie. Dla każdej krawędzi zapisujemy, który dowód ją zmienił, i co stało się ze składem grup.
Wagi i progi są parametrami konfiguracji (configs/fusion/eval.json), nie są dopasowywane do danych."""
from __future__ import annotations

import hashlib
import json
import re
from itertools import combinations
from pathlib import Path
from typing import Any, Mapping, Optional

from methadata.baseline.core import _natural, corpus_fingerprint, select_corpus
from methadata.clustering.fusion.core import cluster_config, collect_pairs, fuse_and_cluster, resolve_config
from methadata.contracts import discover
from methadata.ingest import Store
from methadata.runner import run_experiment

EVAL_VERSION = "1"


class EvalError(Exception):
    """Błędna konfiguracja oceny lub wejście niezgodne z oczekiwaniem."""


def load_eval_config(path: str | Path) -> dict:
    cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    for key in ("families", "profiles", "threshold", "extractors", "reference"):
        if key not in cfg:
            raise EvalError(f"brak sekcji {key!r} w konfiguracji oceny")
    for name, p in cfg["profiles"].items():
        miss = [f for f in p["families"] if f not in cfg["families"]]
        if miss:
            raise EvalError(f"profil {name}: nieznane rodziny {miss}")
    for name in cfg.get("ablate", []):
        if name not in cfg["profiles"]:
            raise EvalError(f"ablate: nieznany profil {name}")
    return cfg


def _profile_config(cfg: Mapping[str, Any], name: str, families: list[str], min_support: int, dataset_version: str,
                    threshold: Optional[float] = None, ungated: bool = False) -> dict:
    fams = {f: ({**cfg["families"][f], "support_min": 1e-9} if ungated else cfg["families"][f]) for f in families}
    return resolve_config({"families": fams,
                           "threshold": threshold if threshold is not None else cfg["threshold"],
                           "coverage_min": cfg.get("coverage_min", 0.5),
                           "min_support": min(min_support, len(families)) if families else min_support,
                           "similarity_version": f"fusion-eval-v{EVAL_VERSION}({name})",
                           "dataset_version": dataset_version})


def _fuse(obs, raw, fcfg, label, ref_units, conf_units, seed):
    res = fuse_and_cluster(obs, raw, fcfg, seed=seed)
    thr = fcfg["threshold"]
    edges = {}
    for p, pf in res.fusions.items():
        a, b = sorted((label[p[0]], label[p[1]]), key=_natural)
        d = pf.details()
        edges[(a, b)] = {"score": d["score"], "fused": d["fused"], "status": d["status"], "coverage": d["coverage"],
                         "supporting_origins": d["supporting_origins"],
                         "accepted": d["status"] == "ok" and d["score"] is not None and d["score"] >= thr,
                         "origins": {o["origin"]: o["score"] for o in d["origins"]}}
    groups = _groups(res, label)
    singles = sorted((label[a.material_id] for a in res.run.assignments if not a.cluster_ids), key=_natural)
    out = {"groups": groups, "singletons": singles, "edges": edges, "threshold": thr,
           "config": {"families": {k: {x: v[x] for x in ("method_id", "weight", "origin", "support_min")}
                                   for k, v in fcfg["families"].items()},
                      "min_support": fcfg["min_support"], "coverage_min": fcfg["coverage_min"], "threshold": thr},
           "run_id": res.run.run_id}
    out["metrics"] = _metrics(out, ref_units, conf_units)
    return out, res


def _groups(res, label):
    return sorted((sorted((label[m] for m in c.members), key=_natural) for c in res.clusters),
                  key=lambda g: _natural(g[0]))


def _metrics(r, ref_units, conf_units):
    edges = r["edges"]
    page_edges = {k: v for k, v in edges.items() if not k[0].startswith("url-") and not k[1].startswith("url-")}
    total = len(edges)
    no_cov = sum(1 for e in edges.values() if e["status"] in ("no_evidence", "insufficient_coverage"))
    no_cov_pages = sum(1 for e in page_edges.values() if e["status"] in ("no_evidence", "insufficient_coverage"))
    out = {"pairs": total, "pairs_pages": len(page_edges), "accepted_edges": sum(1 for e in edges.values() if e["accepted"]),
           "groups": len(r["groups"]), "coverage_loss": round(no_cov / total, 4) if total else 0.0,
           "coverage_loss_pages": round(no_cov_pages / len(page_edges), 4) if page_edges else 0.0,
           "no_support": sum(1 for e in edges.values() if e["status"] == "insufficient_support")}
    for key, units in (("vs_template_ref", ref_units), ("vs_campaign_ref", conf_units)):
        unit = {m: i for i, g in enumerate(units) for m in g}
        false_e = sorted([list(k) for k, e in edges.items() if e["accepted"] and (k[0] not in unit or unit.get(k[0]) != unit.get(k[1]))])
        missed = sorted([[a, b] for g in units for a, b in combinations(sorted(g, key=_natural), 2)
                         if not edges.get((a, b), edges.get((b, a), {})).get("accepted")])
        merged = [g for g in r["groups"] if len({unit.get(m, "s:" + m) for m in g}) > 1]
        exact = sum(1 for g in units if sorted(g, key=_natural) in r["groups"])
        out[key] = {"false_edges": false_e, "missed_edges": missed, "merged_groups": merged,
                    "reference_groups_recovered": f"{exact}/{len(units)}"}
    return out


def _diff(prev, new):
    """Zmiana krawędzi i grup między dwoma profilami: co zmieniło krawędź i jak zmienił się skład grup."""
    changed = []
    score_changes = 0
    for k in sorted(new["edges"], key=lambda k: (_natural(k[0]), _natural(k[1]))):
        a, b = prev["edges"].get(k), new["edges"][k]
        if a is None:
            continue
        sa, sb = a["score"], b["score"]
        if (sa is None) != (sb is None) or (sa is not None and abs(sa - sb) > 1e-6):
            score_changes += 1
        if a["accepted"] != b["accepted"] or a["status"] != b["status"]:
            changed.append({"pair": list(k), "was": {"score": sa, "status": a["status"], "accepted": a["accepted"]},
                            "now": {"score": sb, "status": b["status"], "accepted": b["accepted"]},
                            "evidence_before": a["origins"], "evidence_after": b["origins"],
                            "cause": _cause(a, b)})
    pg, ng = {tuple(g) for g in prev["groups"]}, {tuple(g) for g in new["groups"]}
    return {"edge_changes": changed, "pairs_with_score_change": score_changes,
            "groups_lost": [list(g) for g in sorted(pg - ng, key=lambda g: _natural(g[0]))],
            "groups_gained": [list(g) for g in sorted(ng - pg, key=lambda g: _natural(g[0]))],
            "groups_unchanged": len(pg & ng)}


def _cause(a, b):
    gone = sorted(set(a["origins"]) - set(b["origins"]))
    if a["accepted"] and not b["accepted"]:
        why = f"status {b['status']}"
        if gone:
            why += f"; usunięte źródło: {', '.join(gone)}"
        return f"krawędź utracona ({why}); wsparcie {a['supporting_origins']} -> {b['supporting_origins']}, pokrycie {a['coverage']} -> {b['coverage']}"
    if b["accepted"] and not a["accepted"]:
        return f"krawędź dodana; wsparcie {a['supporting_origins']} -> {b['supporting_origins']}, wynik {a['score']} -> {b['score']}"
    return f"status {a['status']} -> {b['status']}"


def run_eval(store_dir: str | Path, baseline_cfg: Mapping[str, Any], cfg: Mapping[str, Any], *,
             adapters: Optional[Mapping[str, Any]] = None) -> dict:
    store = Store(store_dir)
    cc = baseline_cfg.get("corpus", {})
    pat = cc.get("label_pattern", r"dir:(.+)")
    corpus, excluded, urls = select_corpus(store, cc)
    if not corpus:
        raise EvalError("pusty korpus")
    fp = corpus_fingerprint(corpus, pat)
    dataset_version = f"{baseline_cfg.get('dataset_name', 'dataset')}@{fp}"
    adapters = adapters if adapters is not None else discover(strict=False)
    missing = [m for m in [*cfg["extractors"], *(f["method_id"] for f in cfg["families"].values())] if m not in adapters]
    if missing:
        raise EvalError(f"brak adapterów (zainstaluj extras i entry points): {missing}")
    seed = int(baseline_cfg.get("run", {}).get("seed", 0))

    # etykiety: strony po wzorcu z konfiguracji bazowej, URL-e jako url-N (kolejność wg source_ref)
    label: dict[str, str] = {}
    for o in corpus:
        m = re.fullmatch(pat, o.source_ref)
        label.setdefault(o.canonical_id, set()).add(m.group(1) if m else o.source_ref)
    label = {c: "|".join(sorted(v)) for c, v in label.items()}
    url_info = {}
    for i, o in enumerate(sorted(urls, key=lambda o: o.source_ref), 1):
        label[o.canonical_id] = f"url-{i}"
        url_info[f"url-{i}"] = {"source_ref_sha256": hashlib.sha256(o.source_ref.encode()).hexdigest()[:12],
                                "observation_id": o.observation_id}

    base = {"dataset_version": dataset_version, "seed": seed, "extractors": list(cfg["extractors"]), "comparators": [],
            "clusterer": "components-threshold-v1", "relation": "campaign_candidate",
            "cluster_config": {"threshold": cfg["threshold"], "similarity_version": "fusion-eval-inputs",
                               "method_id": "fusion-v1", "dataset_version": dataset_version,
                               "relation": "campaign_candidate"}}
    ext = run_experiment(base, [*corpus, *urls], adapters, reader=store)   # tylko cechy, pary liczymy niżej
    obs = ext["bundle"].observations
    by_obs: dict[str, list] = {}
    for f in ext["bundle"].features:
        by_obs.setdefault(f.observation_id, []).append(f)
    comps = [adapters[m] for m in sorted({f["method_id"] for f in cfg["families"].values()})]
    raw = collect_pairs(obs, by_obs, comps, run_id="fusion-eval-inputs")

    ref_units = [sorted(g, key=_natural) for g in cfg["reference"]["families"]]
    conf_units = [sorted(g, key=_natural) for g in cfg["reference"].get("campaign_confirmed", cfg["reference"]["families"])]
    present = set(label.values())
    for g in ref_units:
        if not set(g) <= present:
            raise EvalError(f"grupa odniesienia {g} nie występuje w korpusie")

    results: dict[str, dict] = {}
    parent: dict[str, str] = {}
    fused_res = {}

    def add(name, families, min_support, hypothesis, par=None):
        fc = _profile_config(cfg, name, families, min_support, dataset_version)
        r, res = _fuse(obs, raw, fc, label, ref_units, conf_units, seed)
        r["hypothesis"], r["families_on"] = hypothesis, list(families)
        if cfg.get("probe_threshold"):
            pc = _profile_config(cfg, name, families, min_support, dataset_version, cfg["probe_threshold"])
            pr = fuse_and_cluster(obs, raw, pc, seed=seed)
            pg = _groups(pr, label)
            unit = {m: i for i, g in enumerate(ref_units) for m in g}
            r["probe"] = {"threshold": cfg["probe_threshold"], "groups": pg,
                          "merged_groups": [g for g in pg if len({unit.get(m, "s:" + m) for m in g}) > 1]}
            uc = _profile_config(cfg, name, families, 1, dataset_version, cfg["probe_threshold"], ungated=True)
            ug = _groups(fuse_and_cluster(obs, raw, uc, seed=seed), label)   # bez bramki wsparcia: sam próg jak w F04
            r["probe_ungated"] = {"threshold": cfg["probe_threshold"], "groups": ug,
                                  "merged_groups": [g for g in ug if len({unit.get(m, "s:" + m) for m in g}) > 1]}
        results[name] = r
        fused_res[name] = res
        if par:
            parent[name] = par

    for name, p in cfg["profiles"].items():
        add(name, p["families"], p.get("min_support", 2), p.get("hypothesis", ""))
    for pname in cfg.get("ablate", []):
        p = cfg["profiles"][pname]
        for fam in p["families"]:
            rest = [f for f in p["families"] if f != fam]
            add(f"{pname}-bez-{fam}", rest, p.get("min_support", 2), p.get("hypothesis", ""), par=pname)
            results[f"{pname}-bez-{fam}"]["removed_family"] = fam

    diffs = {n: {"against": par, **_diff(results[par], results[n])} for n, par in parent.items()}
    for a, b in cfg.get("compare", [["baseline", "szablon"], ["baseline", "kampania"], ["szablon", "kampania"]]):
        if a in results and b in results:
            diffs[f"{a}->{b}"] = {"against": a, **_diff(results[a], results[b])}

    report = {
        "eval_version": EVAL_VERSION, "dataset_version": dataset_version, "config_name": cfg.get("name"),
        "corpus": {"materials": sorted([v for v in label.values() if not v.startswith("url-")], key=_natural),
                   "urls": url_info, "excluded": excluded},
        "profiles": {n: {k: v for k, v in r.items()} for n, r in results.items()},
        "diffs": diffs,
        "contribution": _contribution(cfg, results),
        "controls": _controls(cfg, results, label),
        "failure_cases": _failures(cfg, raw, label, results, ref_units),
        "examples": _examples(results),
    }
    for r in report["profiles"].values():
        r["edges"] = [{"pair": list(k), **v} for k, v in sorted(r["edges"].items(),
                                                                 key=lambda kv: (-(kv[1]["score"] or -1), _natural(kv[0][0]), _natural(kv[0][1])))
                      if v["accepted"] or v["status"] == "insufficient_support" or v["fused"] and v["fused"] >= 0.3]
    report["recommendation"] = _recommendation(report)
    markdown = render_markdown(report)
    return {"report": report, "markdown": markdown, "results": results, "fused": fused_res, "bundle": ext["bundle"],
            "raw": raw, "label": label, "seed": seed, "dataset_version": dataset_version}   # wejścia dla E18


def _contribution(cfg, results):
    out = {}
    for pname in cfg.get("ablate", []):
        base = results[pname]
        rows = {}
        for fam in cfg["profiles"][pname]["families"]:
            r = results[f"{pname}-bez-{fam}"]
            lost = sorted([list(k) for k, e in base["edges"].items() if e["accepted"] and not r["edges"][k]["accepted"]])
            gained = sorted([list(k) for k, e in base["edges"].items() if not e["accepted"] and r["edges"][k]["accepted"]])
            rows[fam] = {
                "accepted_edges_lost": lost, "accepted_edges_gained": gained,
                "false_edges_delta_template_ref": len(r["metrics"]["vs_template_ref"]["false_edges"]) - len(base["metrics"]["vs_template_ref"]["false_edges"]),
                "missed_edges_delta_template_ref": len(r["metrics"]["vs_template_ref"]["missed_edges"]) - len(base["metrics"]["vs_template_ref"]["missed_edges"]),
                "coverage_loss_delta_pages": round(r["metrics"]["coverage_loss_pages"] - base["metrics"]["coverage_loss_pages"], 4),
                "groups_before": len(base["groups"]), "groups_after": len(r["groups"])}
        out[pname] = rows
    return out


def _controls(cfg, results, label):
    out = {}
    names = [n for n in results]
    for cname, members in cfg.get("control_cases", {}).items():
        rows = {}
        for n in names:
            r = results[n]
            grp = next((g for g in r["groups"] if members[0] in g), None)
            pairs = {f"{a}-{b}": {k: r["edges"][(a, b)][k] for k in ("score", "status", "accepted", "origins")}
                     for a, b in combinations(sorted(members, key=_natural), 2) if (a, b) in r["edges"]}
            rows[n] = {"together": bool(grp and set(members) <= set(grp)),
                       "group": grp or [members[0]], "pairs": pairs}
        out[cname] = rows
    urls = sorted(v for v in label.values() if v.startswith("url-"))
    uo = {}
    for n in names:
        r = results[n]
        st: dict[str, int] = {}
        for k, e in r["edges"].items():
            if any(x in urls for x in k):
                st[e["status"]] = st.get(e["status"], 0) + 1
        uo[n] = {"statuses": st, "singletons": [u for u in urls if u in r["singletons"]],
                 "pairs_with_evidence": sum(1 for k, e in r["edges"].items() if any(x in urls for x in k) and e["fused"] is not None)}
    out["url-only"] = uo
    return out


def _failures(cfg, raw, label, results, ref_units):
    """Przypadki zawodności: (a) najwyższy wynik pojedynczej rodziny poza rodziną odniesienia, (b) most przy niskim progu."""
    unit = {m: i for i, g in enumerate(ref_units) for m in g}
    worst = {}
    for fam, fc in cfg["families"].items():
        best = None
        for p in raw:
            if p.method_id != fc["method_id"] or p.score is None:
                continue
            a, b = sorted((label[p.pair[0]], label[p.pair[1]]), key=_natural)
            if a in unit and unit.get(a) == unit.get(b):
                continue
            if best is None or p.score > best["score"]:
                best = {"pair": [a, b], "score": round(p.score, 4)}
        worst[fam] = best
    probe = {n: r.get("probe") for n, r in results.items() if r.get("probe") and "-bez-" not in n}
    ungated = {n: r.get("probe_ungated") for n, r in results.items() if r.get("probe_ungated") and "-bez-" not in n}
    return {"highest_family_score_outside_reference": worst, "probe_low_threshold": probe,
            "probe_low_threshold_without_support_gate": ungated}


def _examples(results):
    full = results.get("kampania")
    if not full:
        return {}
    ex = {}
    for k, key in (("match", ("p2", "p7")), ("narrative_without_assets", ("p3", "p4"))):
        e = full["edges"].get(key)
        if e:
            ex[k] = {"pair": list(key), **{x: e[x] for x in ("score", "status", "coverage", "supporting_origins", "origins")}}
    return ex


def _recommendation(rep):
    P = rep["profiles"]
    base, camp, strict, tpl = P.get("baseline"), P.get("kampania"), P.get("kampania-scisla"), P.get("szablon")
    lines = []
    if tpl:
        lines.append("Szablon (zasoby + DOM, wsparcie >= 1): grupy " + _fg(tpl["groups"]) +
                     f"; fałszywe krawędzie wg odniesienia szablonu: {len(tpl['metrics']['vs_template_ref']['false_edges'])}.")
    if camp:
        extra = [g for g in camp["groups"] if g not in P["baseline"]["groups"]]
        lines.append("Kampania (zasoby + tekst + DOM, wsparcie >= 2): grupy " + _fg(camp["groups"]) +
                     "; grupy bez poparcia w grafikach: " + (_fg(extra) or "brak") + ".")
    if strict:
        lines.append("Kampania ścisła (wsparcie >= 3): grupy " + _fg(strict["groups"]) + ".")
    lines.append("Rekomendacja dla R02: pokazywać dwa osobne widoki wyniku. (1) „Wspólny szablon” z profilu szablon (zasoby + DOM): "
                 "wynik poparty strukturą i plikami. (2) „Hipoteza kampanii” tylko z profilu kampania-ścisła (wymaga trzech niezależnych "
                 "źródeł) albo, dla profilu kampania, z jawnym oznaczeniem krawędzi popartych wyłącznie tekstem i DOM jako "
                 "podobieństwo narracji/szablonu, nie dowód wspólnego nadawcy. Krawędzie o statusie insufficient_support i no_evidence "
                 "pokazywać z wyjaśnieniem (co porównano, czego zabrakło), nie ukrywać. Progi i wagi pozostają ustawieniami wyjściowymi: "
                 "korpus ma 14 materiałów i trzy znane rodziny, więc nie nadaje się do dostrojenia ani do wniosków o uogólnieniu.")
    return lines


def _fg(groups):
    return "[" + "; ".join("{" + ",".join(g) + "}" for g in groups) + "]" if groups else ""


def render_markdown(rep: Mapping[str, Any]) -> str:
    P = rep["profiles"]
    L = [f"# Ocena fuzji F06 ({rep['config_name']}, dane {rep['dataset_version']})", "",
         f"Materiały: {len(rep['corpus']['materials'])}; URL-only: {len(rep['corpus']['urls'])}. "
         "Znane przypadki (p2/p7/p15, p3/p4, URL-only) to próbka kontrolna znana z planu, nie dowód uogólnienia.", "",
         "## Profile", "", "| profil | hipoteza | rodziny | grupy | krawędzie | utrata pokrycia (strony / wszystkie) | pary bez wsparcia (także niepowiązane) | fałszywe krawędzie (szablon / kampania) | zmergowane grupy |",
         "|---|---|---|---|---|---|---|---|---|"]
    for n, r in P.items():
        m = r["metrics"]
        L.append(f"| {n} | {r.get('hypothesis', '')} | {'+'.join(r['families_on'])} | {_fg(r['groups']) or '-'} | {m['accepted_edges']} | "
                 f"{m['coverage_loss_pages']:.2f} / {m['coverage_loss']:.2f} | {m['no_support']} | "
                 f"{len(m['vs_template_ref']['false_edges'])} / {len(m['vs_campaign_ref']['false_edges'])} | "
                 f"{_fg(m['vs_template_ref']['merged_groups']) or '-'} |")
    L += ["", "## Wkład rodzin (wyłączenie jednej rodziny)", ""]
    for pname, rows in rep["contribution"].items():
        L += [f"Profil {pname}: grupy {_fg(P[pname]['groups'])}", "",
              "| bez rodziny | utracone krawędzie | dodane krawędzie | zmiana fałszywych | zmiana brakujących | zmiana utraty pokrycia (strony) | grupy po |", "|---|---|---|---|---|---|---|"]
        for fam, x in rows.items():
            L.append(f"| {fam} | {_el(x['accepted_edges_lost'])} | {_el(x['accepted_edges_gained'])} | {x['false_edges_delta_template_ref']:+d} | "
                     f"{x['missed_edges_delta_template_ref']:+d} | {x['coverage_loss_delta_pages']:+.2f} | {_fg(P[pname + '-bez-' + fam]['groups']) or '-'} |")
        L.append("")
    L += ["## Zmiany krawędzi i grup", ""]
    for n, d in rep["diffs"].items():
        L.append(f"### {n} (względem {d['against']}): zmiany score {d['pairs_with_score_change']}, grupy niezmienione {d['groups_unchanged']}, "
                 f"utracone {_fg(d['groups_lost']) or '-'}, nowe {_fg(d['groups_gained']) or '-'}")
        for c in d["edge_changes"]:
            L.append(f"- {c['pair'][0]}-{c['pair'][1]}: {c['cause']}; dowody przed {c['evidence_before']} po {c['evidence_after']}")
        L.append("")
    L += ["## Próbka kontrolna", ""]
    for cname, rows in rep["controls"].items():
        L.append(f"### {cname}")
        if cname == "url-only":
            for n, x in rows.items():
                if "-bez-" in n:
                    continue
                L.append(f"- {n}: statusy par {x['statuses']}, pary z dowodem {x['pairs_with_evidence']}, singletony {x['singletons']}")
        else:
            for n, x in rows.items():
                if "-bez-" in n:
                    continue
                L.append(f"- {n}: razem={x['together']}, grupa {x['group']}, " +
                         "; ".join(f"{k} wynik {v['score']} {v['status']}" for k, v in x["pairs"].items()))
        L.append("")
    L += ["## Przypadki zawodności", "", f"Najwyższy wynik pojedynczej rodziny poza rodziną odniesienia: {rep['failure_cases']['highest_family_score_outside_reference']}", ""]
    for key, title in (("probe_low_threshold_without_support_gate", "bez bramki wsparcia (sam próg)"),
                       ("probe_low_threshold", "z bramką wsparcia")):
        for n, p in rep["failure_cases"][key].items():
            L.append(f"- próg {p['threshold']}, {title}, profil {n}: grupy {_fg(p['groups'])}; zmergowane {_fg(p['merged_groups']) or 'brak'}")
    L += ["", "## Rekomendacja dla R02", "", *rep["recommendation"], ""]
    return "\n".join(L)


def _el(edges):
    return ", ".join(f"{a}-{b}" for a, b in edges) or "-"


def write_outputs(res: Mapping[str, Any], out_dir: str | Path) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "fusion_eval.json").write_text(json.dumps(res["report"], indent=1, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    (out / "fusion_eval.md").write_text(res["markdown"], encoding="utf-8")
    return out
