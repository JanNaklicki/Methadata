"""Generowanie czytelnych podsumowań wykrytych grup kampanii (LLM + cache + fallback)."""
from __future__ import annotations

import hashlib
import json
import logging
import os
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from .errors import safe_text
from .storage import atomic_write_json, read_json

log = logging.getLogger(__name__)

DEFAULT_MODEL = "gpt-4o-mini"
PROMPT_VERSION = "v2-short"
GENERIC_SUMMARY = "Hipoteza kampanii na podstawie podobieństwa grafik, tekstu i struktury stron."
FAMILY_PL = {"asset": "grafiki", "text": "tekst", "dom": "struktura HTML"}


def llm_enabled() -> bool:
    """Podsumowania przez OpenAI są domyślnie wyłączone: dane materiałów opuszczają komputer tylko po świadomym włączeniu."""
    return os.environ.get("METHADATA_ENABLE_LLM") == "1" and os.environ.get("METHADATA_DISABLE_LLM") != "1"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_openai_key() -> str:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if key:
        return key
    for candidate in (Path.cwd() / ".env", _repo_root() / ".env"):
        if not candidate.is_file():
            continue
        try:
            for raw in candidate.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() == "OPENAI_API_KEY":
                    return v.strip().strip('"').strip("'")
        except OSError:
            continue
    return ""


def _build_dossier(bundle, cluster: Mapping[str, Any], material_labels: Mapping[str, str],
                   edge_families: Mapping[str, Sequence[str]]) -> dict[str, Any]:
    member_ids = set(cluster["material_ids"])
    obs_to_canon = {o.observation_id: o.canonical_id for o in bundle.observations}

    titles: dict[str, list[str]] = defaultdict(list)
    bodies: dict[str, list[str]] = defaultdict(list)
    domains: dict[str, set[str]] = defaultdict(set)
    trackers: set[str] = set()

    for feature in bundle.features:
        cid = obs_to_canon.get(feature.observation_id)
        if cid not in member_ids:
            continue
        key = (feature.family, feature.kind)
        if key == ("lexical", "text_field") and isinstance(feature.value, Mapping):
            field = str(feature.value.get("field", "")).lower()
            text = safe_text(str(feature.value.get("text", "")), 220)
            if not text or len(text) < 8:
                continue
            if field == "title" and len(titles[cid]) < 2 and text not in titles[cid]:
                titles[cid].append(text)
            elif field == "body" and len(text) >= 35 and len(bodies[cid]) < 3 and text not in bodies[cid]:
                bodies[cid].append(text)
        elif key == ("url_har", "host_order") and isinstance(feature.value, list):
            for host in feature.value[:4]:
                h = safe_text(str(host), 120)
                if h:
                    domains[cid].add(h)
        elif key == ("url_har", "integration_id") and isinstance(feature.value, Mapping):
            vendor = feature.value.get("vendor", "")
            tid = feature.value.get("id", "")
            if vendor and tid:
                trackers.add(safe_text(f"{vendor}:{tid}", 100))

    accepted_ids = {e.pair_evidence_id for e in bundle.runs[0].edges} if bundle.runs else set()
    edge_scores: list[float] = []
    matched_families: set[str] = set()
    for pair in bundle.pairs:
        if pair.pair_evidence_id not in accepted_ids or pair.score is None:
            continue
        if set(pair.pair).issubset(member_ids):
            edge_scores.append(round(float(pair.score), 4))
            for fam in edge_families.get(pair.pair_evidence_id, ()):
                matched_families.add(fam)

    all_domains = sorted({d for ds in domains.values() for d in ds})[:8]
    sample_materials = []
    for cid in sorted(member_ids)[:6]:
        sample_materials.append({
            "label": material_labels.get(cid, cid),
            "titles": titles.get(cid, [])[:2],
            "snippets": bodies.get(cid, [])[:2],
            "domains": sorted(domains.get(cid, set()))[:3],
        })

    return {
        "material_count": len(member_ids),
        "edge_count": len(edge_scores),
        "max_score": max(edge_scores) if edge_scores else None,
        "min_score": min(edge_scores) if edge_scores else None,
        "matched_families": sorted(matched_families),
        "shared_domains": all_domains,
        "trackers": sorted(trackers)[:6],
        "materials": sample_materials,
    }


def _noun(count: int) -> str:
    if count == 1:
        return "materiał"
    return "materiały" if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14 else "materiałów"


def _fallback_summary(dossier: Mapping[str, Any]) -> str:
    """Deterministyczne podsumowanie bez LLM; gdy nie znamy rodzin cech, zostaje ogólna hipoteza zamiast zgadywania."""
    families = [FAMILY_PL.get(f, f) for f in dossier.get("matched_families", ())]
    if not families:
        return GENERIC_SUMMARY
    count = dossier["material_count"]
    score = f" (najwyższe podobieństwo {round(dossier['max_score'] * 100, 1):g}%)".replace(".", ",") \
        if dossier.get("max_score") is not None else ""
    return f"Grupa obejmuje {count} {_noun(count)}; wspólne ślady: {', '.join(families)}{score}."


def _call_openai(dossier: Mapping[str, Any], api_key: str, model: str) -> str | None:
    try:
        import httpx
    except ImportError:
        return None
    system_prompt = (
        "Jesteś analitykiem CERT/NASK w systemie Methadata. "
        "Napisz JEDNO bardzo zwięzłe zdanie po polsku (maks. 120–150 znaków, bez markdownu i bez ogólników) "
        "podsumowujące grupę materiałów: główny temat/narrację (z nagłówków/tekstu) oraz kluczowy wspólny ślad "
        "(np. grafiki, szablon HTML, domeny, Meta Pixel). Nie zmyślaj faktów spoza JSON."
    )
    payload = {
        "model": model,
        "temperature": 0.2,
        "max_tokens": 80,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(dossier, ensure_ascii=False)},
        ],
    }
    try:
        resp = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=httpx.Timeout(12.0, connect=3.0),
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"].strip()
        return safe_text(content, 200) if content else None
    except Exception as exc:  # noqa: BLE001
        log.warning("LLM summary unavailable (%s); falling back to deterministic summary", type(exc).__name__)
        return None


def summarize_clusters(bundle, graph: dict[str, Any], cache_path: Path) -> None:
    """Uzupełnia pole `summary` dla każdego klastra (LLM z cache na dysku lub deterministyczny fallback)."""
    clusters = graph.get("clusters") or []
    if not clusters or bundle is None:
        return

    edge_families = (graph.get("investigation") or {}).get("edge_families") or {}
    material_labels = {m["material_id"]: m["label"] for m in graph.get("materials", ())}
    model = os.environ.get("METHADATA_LLM_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    api_key = _load_openai_key() if llm_enabled() else ""

    cache: dict[str, str] = read_json(cache_path) or {}
    cache_dirty = False

    for cluster in clusters:
        dossier = _build_dossier(bundle, cluster, material_labels, edge_families)
        digest = hashlib.sha256(
            json.dumps(
                {"v": PROMPT_VERSION, "model": model if api_key else "fallback", "dossier": dossier},
                sort_keys=True, ensure_ascii=False,
            ).encode()
        ).hexdigest()[:24]

        if digest in cache:
            cluster["summary"] = cache[digest]
            continue

        summary = _call_openai(dossier, api_key, model) if api_key else None
        if summary:
            cache[digest] = summary
            cache_dirty = True
            cluster["summary"] = summary
        else:
            cluster["summary"] = _fallback_summary(dossier)

    if cache_dirty:
        try:
            atomic_write_json(cache_path, cache)
        except OSError:
            pass
