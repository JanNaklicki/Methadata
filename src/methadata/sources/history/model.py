"""Wersja strony: treść + czas pobrania + źródło. Dwie osie czasu: captured_at (kiedy treść była widoczna)
i known_at (kiedy zgłoszenie/kopia trafiły do nas) - dzięki temu spóźnione zgłoszenie starej wersji nie zmienia
stanu odtworzonego na wcześniejszy dzień."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

_ARCH_TS = re.compile(r"^\d{14}$")


def parse_time(s: Optional[str]) -> Optional[datetime]:
    """ISO-8601 (Z / offset) lub znacznik archiwum YYYYMMDDhhmmss; None dla braku/śmieci. Zawsze UTC."""
    if not s:
        return None
    s = str(s).strip()
    try:
        if _ARCH_TS.match(s):
            return datetime.strptime(s, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
            s += "T00:00:00+00:00"
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)


def iso(d: Optional[datetime]) -> Optional[str]:
    return d.strftime("%Y-%m-%dT%H:%M:%SZ") if d else None


def end_of_day(s: str) -> Optional[datetime]:
    """'2026-03-05' -> koniec tego dnia (UTC); pełny znacznik czasu bez zmian."""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s or ""):
        d = parse_time(s)
        return d.replace(hour=23, minute=59, second=59) if d else None
    return parse_time(s)


@dataclass(frozen=True)
class PageVersion:
    url: str                       # znormalizowany adres strony
    captured_at: datetime          # czas pobrania treści
    known_at: datetime             # czas, gdy wersja stała się dostępna w systemie
    source: str                    # np. "intake", "archive:wayback", "report"
    sha256: str
    snapshot: dict[str, Any] = field(compare=False, default_factory=dict)  # wynik analyze.analyze_html
    published_at: Optional[datetime] = None
    observation_id: Optional[str] = None
    version: Optional[int] = None  # numer wersji obserwacji w magazynie F02

    @property
    def key(self) -> tuple:
        return (self.url, self.sha256, self.captured_at)

    def to_json(self) -> dict:
        return {"url": self.url, "captured_at": iso(self.captured_at), "known_at": iso(self.known_at),
                "published_at": iso(self.published_at), "source": self.source, "sha256": self.sha256,
                "observation_id": self.observation_id, "version": self.version, "snapshot": self.snapshot}
