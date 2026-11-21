"""Okresy emisji z S01 (delivery_start/stop, family=payer). Data wykonania zrzutu (capture_time) to data obserwacji,
nigdy data emisji: służy tylko do sprawdzenia wiarygodności i jako górna granica okresu bez daty końca (censored)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

DEFAULT_TZ = "Europe/Warsaw"  # strefa dla dat bez przesunięcia (Meta Ad Library podaje same daty)
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _parse(s: str, tz: ZoneInfo, end_of_day: bool) -> Optional[datetime]:
    s = (s or "").strip()
    try:
        if _DATE.match(s):
            d = datetime.fromisoformat(s).replace(tzinfo=tz)
            return d + timedelta(days=1) if end_of_day else d  # data końca włącznie -> koniec doby (wyłącznie)
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=tz)
    except ValueError:
        return None


@dataclass(frozen=True)
class Interval:
    start: datetime            # UTC
    end: datetime              # UTC, wyłącznie
    censored: bool = False     # brak daty końca: end = czas obserwacji (reklama aktywna co najmniej do tego momentu)

    def overlap_ratio(self, o: "Interval") -> float:
        """Część krótszego okresu pokryta wspólnie (0..1)."""
        inter = (min(self.end, o.end) - max(self.start, o.start)).total_seconds()
        short = min((self.end - self.start).total_seconds(), (o.end - o.start).total_seconds())
        return max(0.0, min(1.0, inter / short)) if short > 0 else (1.0 if inter >= 0 else 0.0)

    def gap_days(self, o: "Interval") -> float:
        """Odstęp między okresami w dniach (0 gdy się stykają lub nakładają)."""
        return max(0.0, (max(self.start, o.start) - min(self.end, o.end)).total_seconds() / 86400)

    def to_dict(self) -> dict:
        return {"start": self.start.isoformat().replace("+00:00", "Z"), "end": self.end.isoformat().replace("+00:00", "Z"),
                "censored": self.censored}


def build_interval(start: Optional[str], stop: Optional[str], capture_time: Optional[str], tz_name: str = DEFAULT_TZ):
    """-> (Interval | None, powód). None = daty niewiarygodne (brak/zły format/odwrócona kolejność/start po obserwacji)."""
    tz = ZoneInfo(tz_name)
    s = _parse(start, tz, False) if start else None
    if s is None:
        return None, "brak lub niepoprawna data początku emisji"
    cap = _parse(capture_time, tz, False) if capture_time else None
    if start and cap and s > cap:
        return None, "data początku emisji późniejsza niż data obserwacji"
    censored = not stop
    if stop:
        e = _parse(stop, tz, True)
        if e is None:
            return None, "niepoprawna data końca emisji"
    elif cap is not None:
        e = max(cap, s)
    else:
        return None, "brak daty końca emisji i daty obserwacji (okres nieokreślony)"
    if e < s:
        return None, "data końca emisji wcześniejsza niż początku"
    u = timezone.utc
    return Interval(s.astimezone(u), e.astimezone(u), censored), ""
