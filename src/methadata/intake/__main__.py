"""python -m methadata.intake {file|url|fetch|analyze} --store DIR ARG..."""
import argparse
import json
import sys

from . import analyze_url, fetch_url, submit_files, submit_url, FetchPolicy


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="methadata.intake")
    ap.add_argument("cmd", choices=["file", "url", "fetch", "analyze"])
    ap.add_argument("args", nargs="+")
    ap.add_argument("--store", default="store")
    ap.add_argument("--timeout", type=float, default=10.0)
    ap.add_argument("--max-bytes", type=int, default=10 * 1024 * 1024)
    a = ap.parse_args(argv)
    if a.cmd == "analyze":
        out = [analyze_url(u) for u in a.args]
    elif a.cmd == "file":
        out = submit_files(a.args, a.store).summary()
    elif a.cmd == "url":
        out = [submit_url(u, a.store).summary() for u in a.args]
    else:
        pol = FetchPolicy(timeout_s=a.timeout, max_bytes=a.max_bytes)
        out = [fetch_url(u, a.store, policy=pol).summary() for u in a.args]
    json.dump(out, sys.stdout, ensure_ascii=False, indent=1)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
