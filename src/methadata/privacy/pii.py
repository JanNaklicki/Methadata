"""Wspólne pseudonimizowanie danych wrażliwych (numery, adresy, rachunki, portfele, ID sprzedawców).

Token = HMAC-SHA256(klucz lokalny, przestrzeń|wartość), 20 znaków hex; obok niego zapisuje się identyfikator klucza,
a cechy z różnymi `key_id` nie są porównywane. Gołe SHA dałoby się odwrócić brute-force'em.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from pathlib import Path
from typing import Optional, Union

_REPO = Path(__file__).resolve().parents[3]
_EPHEMERAL: dict[str, bytes] = {}


def key_id(key: bytes) -> str:
    return hashlib.sha256(b"key-id|" + key).hexdigest()[:8]


def pii_key(key: Union[bytes, str, None] = None, key_dir: "str | os.PathLike | None" = None) -> bytes:
    """Kolejność: argument, METHADATA_PII_KEY, plik pii.key w key_dir / METHADATA_PII_KEY_DIR / <repo>/var
    (tworzony raz, 0600; var/ jest w .gitignore). Gdy zapis niemożliwy: klucz procesowy."""
    k = key or os.environ.get("METHADATA_PII_KEY")
    if k:
        return k if isinstance(k, bytes) else k.encode("utf-8")
    d = Path(key_dir or os.environ.get("METHADATA_PII_KEY_DIR") or _REPO / "var")
    f = d / "pii.key"
    try:
        if not f.exists():
            d.mkdir(parents=True, exist_ok=True)
            try:
                fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as fh:
                    fh.write(secrets.token_hex(32).encode())
            except FileExistsError:
                pass
        return f.read_bytes().strip()
    except OSError:
        return _EPHEMERAL.setdefault("k", secrets.token_hex(32).encode())


def pii_token(value: str, namespace: str, key: Optional[bytes] = None) -> tuple[str, str]:
    """-> (token, key_id)."""
    k = key if key is not None else pii_key()
    return hmac.new(k, f"{namespace}|{value}".encode("utf-8"), hashlib.sha256).hexdigest()[:20], key_id(k)


def mask(value: str) -> str:
    v = value.strip()
    return v if len(v) <= 6 else v[:2] + "…" + v[-3:]
