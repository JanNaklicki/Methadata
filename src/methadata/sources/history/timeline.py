"""Oś wersji tej samej strony: dodawanie (deduplikacja), stan na moment, stan znany na dany dzień."""
from __future__ import annotations

from datetime import datetime
from typing import Iterable, Optional

from .model import PageVersion, end_of_day, parse_time


class History:
    def __init__(self, versions: Iterable[PageVersion] = ()):
        self._by_url: dict[str, list[PageVersion]] = {}
        self._keys: dict[tuple, PageVersion] = {}
        for v in versions:
            self.add(v)

    def add(self, v: PageVersion) -> str:
        """'new' | 'duplicate' (ta sama treść i czas pobrania - zachowujemy wcześniej znaną) | 'late' (nowa, ale starsza
        od już znanych: spóźnione zgłoszenie starej wersji; trafia na swoje miejsce wg captured_at)."""
        old = self._keys.get(v.key)
        if old is not None:
            if v.known_at < old.known_at:  # wcześniejsza wiedza wygrywa, bo przybliża moment dostępności
                self._replace(old, v)
            return "duplicate"
        lst = self._by_url.setdefault(v.url, [])
        status = "late" if lst and any(x.captured_at > v.captured_at for x in lst) else "new"
        lst.append(v)
        lst.sort(key=lambda x: (x.captured_at, x.known_at, x.source))
        self._keys[v.key] = v
        return status

    def _replace(self, old: PageVersion, new: PageVersion) -> None:
        lst = self._by_url[old.url]
        lst[lst.index(old)] = new
        self._keys[new.key] = new

    def urls(self) -> list[str]:
        return sorted(self._by_url)

    def versions(self, url: str, *, known_by: Optional[str | datetime] = None) -> list[PageVersion]:
        kb = self._moment(known_by)
        return [v for v in self._by_url.get(url, []) if kb is None or v.known_at <= kb]

    @staticmethod
    def _moment(m) -> Optional[datetime]:
        if m is None or isinstance(m, datetime):
            return m
        d = end_of_day(m)
        if d is None:
            raise ValueError(f"nieczytelny moment: {m!r}")
        return d

    def at(self, url: str, moment: str | datetime, *, known_by: Optional[str | datetime] = None) -> Optional[PageVersion]:
        """Oferta/strona widoczna w danym momencie: ostatnia wersja z captured_at <= moment (opcjonalnie tylko
        z wiedzy dostępnej do known_by). None, gdy nie ma żadnej tak wczesnej wersji (brak danych, nie 'pusta')."""
        m = self._moment(moment)
        best = None
        for v in self.versions(url, known_by=known_by):
            if v.captured_at <= m:
                best = v
        return best

    def snapshot_as_of(self, day: str | datetime) -> dict[str, PageVersion]:
        """Wszystko, co było wiadome w danym dniu i widoczne w tym dniu: {url: wersja}."""
        d = self._moment(day)
        out = {}
        for u in self.urls():
            v = self.at(u, d, known_by=d)
            if v is not None:
                out[u] = v
        return out

    def consecutive_changes(self, url: str, *, known_by=None) -> list[tuple[PageVersion, PageVersion]]:
        vs = self.versions(url, known_by=known_by)
        return list(zip(vs, vs[1:]))
