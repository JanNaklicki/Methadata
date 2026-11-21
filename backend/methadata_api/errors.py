"""Jeden kształt błędów (ErrorResponse) i oczyszczanie tekstów do zwykłego tekstu."""
from __future__ import annotations

import re
from typing import Optional

_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_TAG_CHARS = re.compile(r"[<>]")


def safe_text(value, limit: int = 2000) -> str:
    """Zwykły tekst: bez znaków sterujących i bez nawiasów kątowych (nigdy HTML)."""
    s = _TAG_CHARS.sub("", _CTRL.sub(" ", str(value)))
    return s[:limit]


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, *, details: Optional[list[dict]] = None,
                 retryable: bool = False):
        super().__init__(message)
        self.status, self.code, self.message, self.details, self.retryable = status, code, message, details, retryable

    def body(self) -> dict:
        b: dict = {"code": self.code, "message": self.message}
        if self.details:
            b["details"] = [{"field": d["field"], "message": safe_text(d["message"], 500)} for d in self.details]
        b["retryable"] = self.retryable
        return {"error": b}


def validation(message: str, field: str, detail: str) -> ApiError:
    return ApiError(422, "validation_failed", message, details=[{"field": field, "message": detail}])


def storage_unavailable() -> ApiError:
    return ApiError(503, "storage_unavailable", "Magazyn danych jest chwilowo niedostępny. Spróbuj ponownie za chwilę.",
                    retryable=True)
