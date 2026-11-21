"""Wymienne silniki OCR. Domyślny: RapidOCR (onnxruntime, extra `ocr`); tesseract/easyocr = kolejny backend."""
from __future__ import annotations

import io
from typing import Protocol

from .model import OcrLine, OcrResult


class OcrUnavailable(RuntimeError):
    pass


class OcrEngine(Protocol):
    name: str
    version: str

    def recognize(self, data: bytes) -> OcrResult: ...


def image_size(data: bytes) -> tuple[int, int]:
    from PIL import Image
    with Image.open(io.BytesIO(data)) as im:
        return im.size


class RapidOcrEngine:
    name = "rapidocr-onnxruntime"

    def __init__(self):
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as e:  # brak opcjonalnej zależności
            raise OcrUnavailable("zainstaluj extra: pip install 'methadata[ocr]'") from e
        try:
            from importlib.metadata import version
            self.version = version("rapidocr-onnxruntime")
        except Exception:  # noqa: BLE001
            self.version = "?"
        self._ocr = RapidOCR()

    def recognize(self, data: bytes) -> OcrResult:
        import numpy as np
        from PIL import Image
        with Image.open(io.BytesIO(data)) as im:
            w, h = im.size
            arr = np.asarray(im.convert("RGB"))[:, :, ::-1]  # BGR
        res, _ = self._ocr(arr)
        lines = []
        for box, text, conf in res or []:
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            x0, y0 = max(0.0, min(xs)), max(0.0, min(ys))
            x1, y1 = min(float(w), max(xs)), min(float(h), max(ys))
            if x1 > x0 and y1 > y0 and str(text).strip():
                lines.append(OcrLine(str(text), (x0, y0, x1 - x0, y1 - y0), float(conf)))
        lines.sort(key=lambda l: (l.rect[1], l.rect[0]))
        return OcrResult(w, h, lines, self.name, self.version)


def default_engine() -> OcrEngine:
    return RapidOcrEngine()
