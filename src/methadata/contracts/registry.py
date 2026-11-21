"""Odkrywanie adapterów bez centralnej listy. Dwa mechanizmy:

1. Namespace package `methadata_ext`: wystarczy dodać plik `<katalog_na_sys.path>/methadata_ext/<modul>.py`
   (w katalogu zadania, np. `modules/source-qr/methadata_ext/source_qr.py`; nazwa modułu bez myślnika,
   ID adaptera może mieć myślniki). Moduł eksponuje `ADAPTERS` (lista instancji) lub `get_adapters()`.
2. Entry points grupy `methadata.adapters` (pakiety instalowane osobno); wskazują instancję lub fabrykę.
"""
from __future__ import annotations

import importlib
import pkgutil
from importlib import metadata
from typing import Any, Iterable

from .interfaces import AdapterKind, check_adapter

NAMESPACE = "methadata_ext"
ENTRY_POINT_GROUP = "methadata.adapters"


def _from_object(obj: Any) -> list[Any]:
    if callable(obj) and not hasattr(obj, "spec"):
        obj = obj()
    return list(obj) if isinstance(obj, (list, tuple)) else [obj]


def discover(kind: AdapterKind | None = None, *, strict: bool = True) -> dict[str, Any]:
    """Zwraca {adapter_id: adapter}. strict=False pomija moduły, których import się nie udał
    (np. brak opcjonalnej zależności), zamiast zgłaszać wyjątek."""
    found: dict[str, Any] = {}

    def add(obj: Any, origin: str):
        spec = check_adapter(obj)
        if spec.id in found:
            raise ValueError(f"adapter {spec.id!r} zdefiniowany dwukrotnie ({origin})")
        found[spec.id] = obj

    try:
        ns = importlib.import_module(NAMESPACE)
        modules = [m.name for m in pkgutil.iter_modules(ns.__path__, NAMESPACE + ".")]
    except ModuleNotFoundError:
        modules = []
    for name in sorted(modules):
        try:
            mod = importlib.import_module(name)
            items = mod.get_adapters() if hasattr(mod, "get_adapters") else getattr(mod, "ADAPTERS", [])
            for obj in items:
                add(obj, name)
        except ImportError:
            if strict:
                raise
    for ep in sorted(metadata.entry_points(group=ENTRY_POINT_GROUP), key=lambda e: e.name):
        try:
            for obj in _from_object(ep.load()):
                add(obj, ep.name)
        except ImportError:
            if strict:
                raise
    if kind is not None:
        found = {k: v for k, v in found.items() if v.spec.kind is kind}
    return found
