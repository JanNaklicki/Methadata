"""Publiczny widok W01: ClusterRunView i dowody. Serializuje wyłącznie pola publiczne (bez local_ref, ścieżek, sesji).
Identyfikatory dowodów i artefaktów są rozwiązywane wyłącznie w obrębie jednego przebiegu (jego Bundle)."""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any, Mapping, Optional

from methadata.contracts import Bundle
from methadata.contracts.models import Artifact, ArtifactRole, InputKind, Observation, PairEvidence
from methadata.extractors.media.probe import probe_bytes
from methadata.ingest import Store
from methadata.ingest.detect import sniff

from .errors import safe_text

_PREVIEW_EXT = {"png": "png", "jpeg": "jpg", "gif": "gif", "webp": "webp"}
_PREVIEW_MEDIA = {"png": "image/png", "jpeg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}
PREVIEW_NAME = re.compile(r"^[A-Za-z0-9_.\-]+\.(png|jpg|jpeg|webp|gif)$")
MAX_ITEMS_PER_SIDE = 24
MAX_EXCERPT_CHARS = 300
_TAGS = re.compile(r"<[^>]*>")


# ---------------------------------------------------------------- podglądy
def ensure_preview(store: Store, art: Artifact, alt: str, previews_dir: Path) -> Optional[dict]:
    """Kopiuje obraz (png/jpeg/gif/webp, rozpoznany z zawartości) do katalogu statycznego pod nazwą z hasha treści."""
    if art.local_ref is None or not (art.media_type or "").lower().startswith("image/"):
        return None
    try:
        data = store.blob_path(art.sha256).read_bytes() if store.has_blob(art.sha256) else None
    except OSError:
        return None
    if data is None:
        return None
    kind = sniff(data[:65536], "", len(data)).kind
    if kind not in _PREVIEW_EXT:
        return None
    name = f"{art.sha256[:16]}.{_PREVIEW_EXT[kind]}"
    dest = previews_dir / name
    if not dest.is_file():
        previews_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=previews_dir, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, dest)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    out = {"href": f"/static/previews/{name}", "media_type": _PREVIEW_MEDIA[kind]}
    pr = probe_bytes(data[:2_000_000])
    if pr.width and pr.height:
        out.update(width=int(pr.width), height=int(pr.height))
    out["alt"] = safe_text(alt, 200)
    return out


def _is_image(a: Artifact) -> bool:
    return (a.media_type or "").lower().startswith("image/")


def _thumbnail_artifact(obs: Observation) -> Optional[Artifact]:
    for pick in (lambda a: a.role is ArtifactRole.landing_screenshot, lambda a: a.role is ArtifactRole.creative_image,
                 lambda a: a.role is ArtifactRole.resource and _is_image(a)):
        for a in obs.artifacts:
            if pick(a) and _is_image(a):
                return a
    return None


def default_label(obs: Observation) -> str:
    if obs.input_kind is InputKind.url_only:
        return safe_text(obs.artifacts[0].display_url or obs.source_ref, 300)
    ref = obs.source_ref
    for pre in ("dir:", "file:", "zip:"):
        if ref.startswith(pre):
            ref = ref[len(pre):]
            break
    return safe_text(ref, 300)


def observation_display_url(obs: Observation) -> Optional[str]:
    if obs.input_kind is InputKind.url_only:
        return obs.artifacts[0].display_url
    return None


# ---------------------------------------------------------------- ClusterRunView
def _plural(n: int, one: str, few: str, many: str) -> str:
    if n == 1:
        return one
    return few if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else many


def build_view(run_id: str, bundle: Bundle, labels: Mapping[str, str], store: Store, previews_dir: Path,
               target_observation_id: Optional[str] = None) -> dict:
    cr = bundle.runs[0]
    fusion = "fusion" in cr.config
    obs_by_canon = {o.canonical_id: o for o in bundle.observations}
    families: dict[str, set[str]] = {}
    kinds = {("asset", "image_sha256"): "asset"}
    if fusion:
        kinds.update({("dom_struct", "dom_sequence"): "dom", ("lexical", "text_field"): "text"})
    for f in bundle.features:
        family = kinds.get((f.family, f.kind))
        if family:
            families.setdefault(f.observation_id, set()).add(family)

    materials = []
    for cid in sorted(obs_by_canon):
        o = obs_by_canon[cid]
        label = safe_text(labels.get(o.observation_id) or default_label(o), 300)
        th = _thumbnail_artifact(o)
        thumb = ensure_preview(store, th, f"Podgląd: {label}", previews_dir) if th else None
        materials.append({"material_id": cid, "observation_id": o.observation_id, "input_kind": o.input_kind.value,
                          "label": label, "display_url": observation_display_url(o), "thumbnail": thumb,
                          "available_evidence": sorted(families.get(o.observation_id, ()))})

    groups: dict[str, list[str]] = {}
    for a in cr.assignments:
        for c in a.cluster_ids:
            groups.setdefault(c, []).append(a.material_id)
    clusters = [
        {"cluster_id": cid, "material_ids": sorted(ms), "relation": cr.relation.value,
         "summary": ("Hipoteza kampanii na podstawie podobieństwa grafik, tekstu i struktury stron."
                     if fusion else
                     f"{len(ms)} {_plural(len(ms), 'materiał ma', 'materiały mają', 'materiałów ma')} wspólne grafiki.")}
        for cid, ms in sorted(groups.items())
    ]

    counts = {k: sum(1 for a in cr.assignments if a.status.value == k)
              for k in ("grouped", "singleton", "noise", "insufficient_data")}
    target_canon = next((o.canonical_id for o in bundle.observations if o.observation_id == target_observation_id), None)
    target_status = next((a.status.value for a in cr.assignments if a.material_id == target_canon), None)
    if fusion and target_status == "insufficient_data":
        outcome = "insufficient_evidence"
        msg = "Za mało dowodów dotyczących analizowanego materiału. Widoczne grupy obejmują materiały z indeksu."
    elif clusters:
        outcome = "grouped"
        n = len(clusters)
        msg = f"Znaleziono {n} {_plural(n, 'grupę', 'grupy', 'grup')}: {counts['grouped']} powiązanych materiałów."
        if target_status == "grouped":
            msg += " Analizowany materiał należy do jednej z grup."
        elif target_status == "insufficient_data":
            msg += " Analizowany materiał nie został porównany: brak grafik do zestawienia."
        elif target_status == "singleton":
            msg += " Analizowany materiał nie pasuje do żadnej grupy."
    elif counts["insufficient_data"] and (target_status == "insufficient_data" or counts["singleton"] == 0):
        outcome = "insufficient_evidence"
        msg = "Za mało dowodów: nie udało się porównać materiału z innymi (brak grafik do zestawienia, np. dodano sam adres URL)."
    else:
        outcome = "no_groups"
        msg = ("Żadne porównanie nie spełniło kryteriów fuzji dowodów." if fusion
               else "Nie znaleziono wspólnych grafik: żaden materiał nie połączył się z innymi.")
    summary = {"outcome": outcome, "material_count": len(cr.assignments), **counts, "message": msg}

    def pair_order(p: PairEvidence) -> tuple:
        # W jury najpierw pokaż porównania zgłoszonego materiału.
        return (fusion and target_canon not in p.pair, p.score is None, -(p.score or 0.0), p.pair_evidence_id)

    pairs = sorted((p for p in bundle.pairs if p.run_id == run_id), key=pair_order)
    pair_evidence = [{"evidence_id": p.pair_evidence_id, "pair": list(p.pair), "method_id": p.method_id,
                      "relation": p.relation.value, "score": p.score, "explanation": public_explanation(p),
                      "evidence_url": f"/api/v1/runs/{run_id}/evidence/{p.pair_evidence_id}"} for p in pairs]
    return {"cluster_run": cr.model_dump(mode="json"), "summary": summary, "materials": materials,
            "clusters": clusters, "pair_evidence": pair_evidence}


# ---------------------------------------------------------------- dowód dla pary
def public_explanation(pair: PairEvidence) -> str:
    text = pair.explanation
    if pair.method_id == "fusion-v1":
        text = text.split(" Bez rodziny:", 1)[0]
    return safe_text(text)


def _excerpt(store: Store, art: Artifact, loc: Mapping[str, Any]) -> Optional[dict]:
    if loc.get("kind") != "text_span" or art.local_ref is None:
        return None
    if not ((art.media_type or "").startswith("text/") or art.media_type in ("application/json", "application/xhtml+xml")):
        return None
    try:
        text = store.blob_path(art.sha256).read_bytes()[:4_000_000].decode("utf-8", "replace")
    except OSError:
        return None
    start, end = int(loc["start"]), min(int(loc["end"]), int(loc["start"]) + MAX_EXCERPT_CHARS)
    raw = text[start:end]
    plain = re.sub(r"\s+", " ", _TAGS.sub(" ", raw)).strip()
    plain = safe_text(plain, MAX_EXCERPT_CHARS)
    if not plain:
        return None
    return {"text": plain, "highlight_start": 0, "highlight_end": len(plain), "source_label": "treść strony"}


def build_evidence(run_id: str, bundle: Bundle, view: Mapping[str, Any], evidence_id: str, store: Store,
                   previews_dir: Path) -> Optional[dict]:
    pair: Optional[PairEvidence] = next((p for p in bundle.pairs if p.run_id == run_id and p.pair_evidence_id == evidence_id), None)
    if pair is None:
        return None
    mats = {m["material_id"]: m for m in view["materials"]}
    arts = {a.artifact_id: a for o in bundle.observations for a in o.artifacts}
    available = pair.score is not None
    limitations: list[str] = []
    sides = []
    for cid in pair.pair:
        m = mats.get(cid, {"label": cid, "display_url": None})
        items = []
        refs = pair.evidence_refs.get(cid, []) if available else []
        if len(refs) > MAX_ITEMS_PER_SIDE:
            limitations.append(f"Pokazano {MAX_ITEMS_PER_SIDE} z {len(refs)} fragmentów dowodu dla materiału „{m['label']}”.")
        for ref in refs[:MAX_ITEMS_PER_SIDE]:
            art = arts.get(ref.artifact_id)  # tylko artefakty tego przebiegu
            if art is None:
                continue
            loc = ref.locator.model_dump(mode="json") if ref.locator else None
            ex = _excerpt(store, art, loc) if loc else None
            items.append({"artifact_id": art.artifact_id, "role": art.role.value, "media_type": art.media_type,
                          "display_url": art.display_url, "locator": loc, "text_excerpts": [ex] if ex else [],
                          "preview": ensure_preview(store, art, f"Grafika z materiału {m['label']}", previews_dir)})
        sides.append({"material_id": cid, "label": m["label"], "display_url": m.get("display_url"), "items": items})
    if not available:
        limitations.append(public_explanation(pair))
    return {"evidence_id": pair.pair_evidence_id, "run_id": run_id, "relation": pair.relation.value,
            "method_id": pair.method_id, "score": pair.score, "explanation": public_explanation(pair),
            "available": available, "limitations": limitations, "sides": sides}
