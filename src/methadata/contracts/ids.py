"""Stabilne identyfikatory. Wszystkie są funkcją treści, więc ponowny import daje to samo ID."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterable

ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:\-]{0,127}$"
SHA256_PATTERN = r"^[0-9a-f]{64}$"
_ID_RE = re.compile(ID_PATTERN)

# Role artefaktów, których treść identyfikuje materiał (do canonical_id).
_CONTENT_ROLES = ("landing_html", "landing_screenshot", "creative_image", "har", "video", "audio")


def _h(*parts: str, n: int = 16) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:n]


def is_valid_id(value: str) -> bool:
    return bool(_ID_RE.match(value))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def observation_id(source_ref: str, input_kind: str) -> str:
    """Nadawany przy imporcie źródła: ten sam rekord źródłowy -> to samo ID."""
    return "obs-" + _h("observation", source_ref, input_kind)


def artifact_id(obs_id: str, role: str, sha256: str, origin: str = "") -> str:
    """origin = lokalizacja w źródle (ścieżka/wpis archiwum); te same bajty w dwóch miejscach to dwa wystąpienia."""
    return "art-" + _h("artifact", obs_id, role, sha256, origin)


def feature_id(obs_id: str, family: str, kind: str, extractor_version: str, value: Any) -> str:
    return "ftr-" + _h("feature", obs_id, family, kind, extractor_version,
                        json.dumps(value, sort_keys=True, ensure_ascii=False, default=str))


def canonical_id(artifacts: Iterable[tuple[str, str]], url: str | None = None) -> str:
    """Deduplikacja. Wejście: pary (rola, sha256). Identyczne bajty HTML/zrzutu/HAR -> to samo ID,
    niezależnie od domeny, nazwy pliku i kolejności. Sam URL: skrót znormalizowanego URL."""
    pairs = sorted({(r, s) for r, s in artifacts if r in _CONTENT_ROLES})
    if pairs:
        return "can-" + _h("canonical", *[f"{r}={s}" for r, s in pairs])
    if url is None:
        raise ValueError("brak artefaktów treści i URL – canonical_id niewyznaczalne")
    return "can-" + _h("canonical-url", url)


def pair_evidence_id(run_id: str, pair: tuple[str, str], method_id: str, relation: str) -> str:
    a, b = sorted(pair)
    return "pev-" + _h("pair", run_id, a, b, method_id, relation)
