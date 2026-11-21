"""E10: OCR z prostokątami, podobne zdania, układ tytuł–ilustracja–przycisk, dopasowanie zrzutu do HTML.

Udostępnianie adapterom S: cecha `ocr/ocr_boxes` (JSON: width,height,lines[{text,conf,rect px,rel 0..1}]),
evidence_refs = ImageRect każdej linii; odczyt: `ocr_results(features)`; kopia w magazynie jako artefakt pochodny
(`origin=ocr-boxes:<silnik>`). Komparatory: ocr-sentences-v1, ocr-layout-v1, ocr-html-match-v1."""
from .compare import (OcrHtmlComparator, OcrLayoutComparator, OcrSentenceComparator, error_impact, match_sentences,  # noqa: F401
                      ocr_results, perturb)
from .engine import OcrUnavailable, RapidOcrEngine, default_engine, image_size  # noqa: F401
from .extractor import EXTRACTOR_VERSION, OcrExtractor  # noqa: F401
from .layout import analyze_layout, layout_similarity, sentences  # noqa: F401
from .match import locate_quote, match_to_html  # noqa: F401
from .model import OcrLine, OcrResult, norm  # noqa: F401


def get_adapters():
    return [OcrExtractor(), OcrSentenceComparator(), OcrLayoutComparator(), OcrHtmlComparator()]
