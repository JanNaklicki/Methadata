"""python -m methadata.monitor run --store DIR (--export PATH | --meta-search TERMS) [--config FILE]
Jednorazowy przebieg do uruchamiania z crona/launchd; kod wyjścia 0 = sukces, 1 = błąd (kursor bez zmian)."""
import argparse
import json
import sys

from .analysis import payer_analyzer
from .core import run_once
from .provider import JsonFileProvider, MetaAdLibraryProvider
from .state import StateFile


def _load(path):
    if not path:
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="methadata.monitor")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="jeden przebieg importu z kursora")
    st = sub.add_parser("status", help="pokaż stan (kursor, kolejka analizy)")
    for p in (r, st):
        p.add_argument("--store", required=True)
        p.add_argument("--name", default="meta-ad-library")
    r.add_argument("--config", help="JSON: page_size, max_pages, retries, backoff_s, run (konfiguracja analizy)")
    g = r.add_mutually_exclusive_group(required=True)
    g.add_argument("--export", help="plik/katalog eksportu JSON/JSONL (bez sieci)")
    g.add_argument("--meta-search", help="szukane frazy w Meta Ad Library (wymaga tokenu w META_AD_LIBRARY_TOKEN)")
    r.add_argument("--countries", default="PL")
    r.add_argument("--no-analysis", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "status":
        print(json.dumps(StateFile(a.store, a.name).load().to_dict(), ensure_ascii=False, indent=1))
        return 0
    cfg = _load(a.config)
    prov = (JsonFileProvider(a.export, source_id=a.name) if a.export
            else MetaAdLibraryProvider(a.meta_search, countries=a.countries.split(",")))
    rep = run_once(prov, a.store, name=a.name, analyzer=None if a.no_analysis else payer_analyzer(cfg.get("run")),
                   page_size=cfg.get("page_size", 100), max_pages=cfg.get("max_pages", 10),
                   retries=cfg.get("retries", 2), backoff_s=cfg.get("backoff_s", 2.0))
    json.dump(rep.summary(), sys.stdout, ensure_ascii=False, indent=1)
    print()
    return 0 if rep.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
