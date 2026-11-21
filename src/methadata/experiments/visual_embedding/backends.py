"""Wymienne backendy wektorów. Zależności (torch, open_clip) importowane leniwie; brak -> BackendUnavailable.

 - OpenClipBackend: obraz i tekst w jednej przestrzeni (ViT-B-32, publiczne wagi), cache poza repo.
 - ColorLayoutBackend: bazowy deskryptor koloru i układu (numpy+Pillow). NIE jest semantyczny; służy jako
   punkt odniesienia i do testów logiki bez modelu."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence


class BackendUnavailable(RuntimeError):
    """Brak biblioteki lub wag; komunikat trafia do ExtractionStatus.detail (state=unsupported)."""


class Backend:
    name = ""
    version = ""
    dim = 0
    supports_text = False

    def embed_images(self, images: Sequence[Any]):  # -> ndarray (n, dim), wiersze znormalizowane
        raise NotImplementedError

    def embed_texts(self, texts: Sequence[str]):
        raise BackendUnavailable(f"backend {self.name} nie obsługuje tekstu")


def _norm(m):
    import numpy as np
    n = np.linalg.norm(m, axis=1, keepdims=True)
    return m / np.where(n == 0, 1.0, n)


class ColorLayoutBackend(Backend):
    name, version, dim = "color-layout", "1", 4 * 4 * 3 + 12

    def embed_images(self, images):
        import numpy as np
        from PIL import Image
        rows = []
        for im in images:
            rgb = im.convert("RGB").resize((4, 4), Image.Resampling.BOX)
            grid = np.asarray(rgb, dtype=np.float64).ravel() / 255.0
            hsv = np.asarray(im.convert("RGB").resize((32, 32)).convert("HSV"), dtype=np.float64)
            hue, sat = hsv[..., 0].ravel(), hsv[..., 1].ravel() / 255.0
            hist, _ = np.histogram(hue, bins=12, range=(0, 256), weights=sat)
            hist = hist / max(1e-9, hist.sum())
            rows.append(np.concatenate([grid - 0.5, hist]))
        return _norm(np.vstack(rows))


class OpenClipBackend(Backend):
    supports_text = True

    def __init__(self, cfg: Mapping[str, Any]):
        self.model_name, self.pretrained = cfg["model"], cfg["pretrained"]
        self.name = f"open_clip-{self.model_name}"
        self.version = self.pretrained
        self.dim = 512
        self._cache = os.environ.get(cfg.get("model_cache_env", ""), "") or str(Path(tempfile.gettempdir()) /
                                                                                 "methadata-model-cache")
        self._m: Optional[tuple] = None

    def _load(self):
        if self._m is None:
            try:
                import open_clip
                import torch
                model, _, pre = open_clip.create_model_and_transforms(
                    self.model_name, pretrained=self.pretrained, cache_dir=self._cache)
                tok = open_clip.get_tokenizer(self.model_name)
            except ImportError as e:
                raise BackendUnavailable(f"brak biblioteki ({e.name}); zainstaluj extra visual-embedding") from e
            except Exception as e:  # noqa: BLE001 - brak sieci/wag
                raise BackendUnavailable(f"nie udało się załadować wag {self.model_name}/{self.pretrained}: "
                                         f"{type(e).__name__}") from e
            model.eval()
            self._m = (model, pre, tok, torch)
        return self._m

    def embed_images(self, images):
        model, pre, _, torch = self._load()
        with torch.no_grad():
            x = torch.stack([pre(im.convert("RGB")) for im in images])
            return _norm(model.encode_image(x).float().numpy())

    def embed_texts(self, texts):
        model, _, tok, torch = self._load()
        with torch.no_grad():
            return _norm(model.encode_text(tok(list(texts))).float().numpy())


def make_backend(cfg: Mapping[str, Any]) -> Backend:
    b = cfg["backend"]
    if b == "color-layout":
        return ColorLayoutBackend()
    if b == "open_clip":
        return OpenClipBackend(cfg)
    raise BackendUnavailable(f"nieznany backend {b!r}")
