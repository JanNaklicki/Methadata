"""CLI: python -m methadata.ingest import --store DIR ŹRÓDŁO...   |   python -m methadata.ingest verify --store DIR"""
import argparse
import json
import sys

from .importer import FileImporter
from .manifest import verify_blobs
from .store import Store


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="methadata.ingest")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pi = sub.add_parser("import", help="importuj katalog, plik, ZIP, plik z listą URL lub adres URL")
    pi.add_argument("sources", nargs="+")
    pi.add_argument("--store", required=True)
    pv = sub.add_parser("verify", help="sprawdź spójność blobów magazynu")
    pv.add_argument("--store", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "verify":
        bad = verify_blobs(Store(a.store))
        print("\n".join(bad) or "OK")
        return 1 if bad else 0
    rep = FileImporter().import_sources(a.sources, a.store)
    print(json.dumps(rep.summary(), ensure_ascii=False, indent=1))
    for i in rep.issues:
        if i.severity != "info":
            print(f"[{i.severity}] {i.code} {i.source} {i.origin or ''}: {i.message}", file=sys.stderr)
    if not rep.entries:
        return 2
    return 3 if rep.errors else 0


if __name__ == "__main__":
    sys.exit(main())
