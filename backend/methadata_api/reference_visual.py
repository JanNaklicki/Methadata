"""Dopasowanie wizualne osób publicznych / logotypów referencyjnych (OpenCLIP lub pHash) z katalogu referencyjnego."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


def _reference_dir() -> Path:
    """Katalog z obrazami referencyjnymi: METHADATA_REFERENCE_DIR, potem var/reference_figures, potem data/reference_figures."""
    env = os.environ.get("METHADATA_REFERENCE_DIR", "").strip()
    if env:
        return Path(env).expanduser()
    root = Path(__file__).resolve().parents[2]
    for candidate in (root / "var" / "reference_figures", root / "data" / "reference_figures"):
        if any(_discover_references(candidate)):
            return candidate
    return root / "data" / "reference_figures"


def _discover_references(ref_dir: Path) -> list[tuple[str, Path]]:
    """Zwraca [(etykieta_osoby, ścieżka_obrazu)]. Obsługuje pliki <Imie_Nazwisko>.jpg lub podkatalogi <Imie_Nazwisko>/*.jpg."""
    if not ref_dir.is_dir():
        return []
    items: list[tuple[str, Path]] = []
    for entry in sorted(ref_dir.iterdir()):
        if entry.is_file() and entry.suffix.lower() in IMAGE_EXTS:
            label = entry.stem.replace("_", " ").replace("-", " ").strip()
            if label:
                items.append((label, entry))
        elif entry.is_dir():
            label = entry.name.replace("_", " ").replace("-", " ").strip()
            for child in sorted(entry.iterdir()):
                if child.is_file() and child.suffix.lower() in IMAGE_EXTS and label:
                    items.append((label, child))
    return items


def match_reference_figures(bundle, *, threshold_clip: float = 0.78, threshold_phash: int = 10) -> dict[str, list[dict[str, Any]]]:
    """Dopasowuje obrazy z katalogu referencyjnego do cech OpenCLIP/pHash w `bundle.features`.
    Zwraca {canonical_material_id: [{"person": str, "score": float, "method": str}]}."""
    ref_dir = _reference_dir()
    refs = _discover_references(ref_dir)
    if not refs or bundle is None:
        return {}

    obs_to_canon = {o.observation_id: o.canonical_id for o in bundle.observations}
    matches: dict[str, dict[str, dict[str, Any]]] = {}

    # 1. Spróbuj OpenCLIP, jeśli w bundlu są cechy visual_embedding.
    clip_feats = [f for f in bundle.features if f.family == "visual_embedding" and isinstance(f.value, dict) and f.value.get("crops")]
    if clip_feats:
        try:
            import numpy as np
            from PIL import Image
            from methadata.experiments.visual_embedding.backends import OpenClipBackend
            from methadata.experiments.visual_embedding.config import DEFAULTS

            backend = OpenClipBackend(DEFAULTS)
            ref_vecs: list[tuple[str, Any]] = []
            for label, path in refs:
                with Image.open(path) as im:
                    vec = backend.embed_images([im.convert("RGB")])[0]
                    ref_vecs.append((label, vec))
            for f in clip_feats:
                mid = obs_to_canon.get(f.observation_id)
                if not mid:
                    continue
                for crop in f.value.get("crops", ()):
                    cvec = crop.get("vec")
                    if not cvec:
                        continue
                    arr = np.asarray(cvec, dtype=np.float32)
                    for label, rvec in ref_vecs:
                        sim = float(np.dot(arr, rvec))
                        if sim >= threshold_clip:
                            prev = matches.setdefault(mid, {}).get(label)
                            if prev is None or sim > prev["score"]:
                                matches[mid][label] = {"person": label, "score": round(sim, 3), "method": "open_clip"}
        except Exception as exc:  # noqa: BLE001
            log.debug("Reference OpenCLIP matching skipped: %s", exc)

    # 2. Fallback pHash (jeśli są cechy perceptual/phash).
    phash_feats = [f for f in bundle.features if f.family == "perceptual" and f.kind == "phash" and isinstance(f.value, dict)]
    if phash_feats:
        try:
            from methadata.experiments.perceptual.phash import hamming, phash_bytes

            ref_hashes: list[tuple[str, int]] = []
            for label, path in refs:
                h = phash_bytes(path.read_bytes())
                if h is not None:
                    ref_hashes.append((label, h))
            for f in phash_feats:
                mid = obs_to_canon.get(f.observation_id)
                val = f.value.get("phash")
                if not mid or val is None:
                    continue
                hv = int(val, 16) if isinstance(val, str) else int(val)
                for label, rh in ref_hashes:
                    dist = hamming(hv, rh)
                    if dist <= threshold_phash:
                        score = round(max(0.0, 1.0 - dist / (2.0 * threshold_phash)), 3)
                        prev = matches.setdefault(mid, {}).get(label)
                        if prev is None or score > prev["score"]:
                            matches[mid][label] = {"person": label, "score": score, "method": "phash"}
        except Exception as exc:  # noqa: BLE001
            log.debug("Reference pHash matching skipped: %s", exc)

    return {mid: sorted(items.values(), key=lambda x: -x["score"]) for mid, items in matches.items()}
