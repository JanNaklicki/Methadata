#!/usr/bin/env python3
"""Zgodność z kontraktem HTTP (contracts/http/openapi.yaml).

  python contracts/http/check_conformance.py                      # sprawdza specyfikację i przykłady
  python contracts/http/check_conformance.py --base-url http://localhost:8000   # + żywy backend (odczyt: health, demo, run, dowody)

Żywy backend: GET /health, GET /demo, POST /runs (z observation_id z /demo), odpytywanie do stanu końcowego,
GET dowodu dla każdej pary oraz błędy 404. Każda odpowiedź jest walidowana schematem z OpenAPI, ClusterRun modelem F01
i skanowana pod kątem wycieków (ścieżki systemowe, aktywny HTML, wartości sesyjne).
Wymaga: pyyaml, jsonschema, openapi-spec-validator, methadata (extras `test`).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

HERE = Path(__file__).resolve().parent
SPEC_PATH = HERE / "openapi.yaml"

_FS_PATH = re.compile(r"^(/(Users|home|tmp|var|etc|private|mnt|opt|root|srv)/|[A-Za-z]:\\|file://|\\\\)")
_ACTIVE_HTML = re.compile(r"<\s*/?\s*(script|html|iframe|object|embed|style|svg|img|a|body|link|meta|form|input)\b|\bon[a-z]+\s*=|javascript:", re.I)
_SESSION = re.compile(r"(set-cookie|cookie:|sessionid|session_id|jsessionid|phpsessid|sid=|bearer\s+\S+|authorization:|access_token|csrf)", re.I)
_FORBIDDEN_KEYS = {"local_ref", "cookie", "cookies", "set-cookie", "authorization", "session", "session_id", "token", "path", "local_path"}


def load_spec() -> dict:
    return yaml.safe_load(SPEC_PATH.read_text(encoding="utf-8"))


def schema_validator(spec: dict, schema: dict) -> Draft202012Validator:
    """Schemat z OpenAPI; $ref '#/components/...' rozwiązywane względem całej specyfikacji."""
    wrapped = {"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": "urn:methadata:openapi", **{k: v for k, v in spec.items() if k == "components"}}
    reg = Registry().with_resource("urn:methadata:openapi", Resource.from_contents(wrapped, DRAFT202012))
    return Draft202012Validator(_inline(schema), registry=reg)


def _inline(schema: dict) -> dict:
    """Przepisuje lokalne $ref '#/...' na 'urn:methadata:openapi#/...'."""
    if isinstance(schema, dict):
        return {k: (("urn:methadata:openapi" + v) if k == "$ref" and isinstance(v, str) and v.startswith("#/") else _inline(v)) for k, v in schema.items()}
    if isinstance(schema, list):
        return [_inline(x) for x in schema]
    return schema


def leaks(value, where: str = "$") -> list[str]:
    """Wycieki w publicznej odpowiedzi: ścieżki systemu plików, aktywny HTML, wartości sesyjne, zakazane klucze."""
    found: list[str] = []
    if isinstance(value, dict):
        for k, v in value.items():
            if str(k).lower() in _FORBIDDEN_KEYS:
                found.append(f"{where}.{k}: zakazany klucz")
            found += leaks(v, f"{where}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            found += leaks(v, f"{where}[{i}]")
    elif isinstance(value, str):
        if _FS_PATH.search(value):
            found.append(f"{where}: ścieżka systemu plików")
        if _ACTIVE_HTML.search(value):
            found.append(f"{where}: aktywny HTML")
        if _SESSION.search(value):
            found.append(f"{where}: wartość sesyjna")
    return found


def check_cluster_run_view(view: dict) -> list[str]:
    """Spójność wewnętrzna ClusterRunView i zgodność ClusterRun z modelem F01."""
    from methadata.contracts.models import ClusterRun
    errs: list[str] = []
    try:
        ClusterRun.model_validate(view["cluster_run"])
    except Exception as e:  # noqa: BLE001
        return [f"ClusterRun (F01): {e}"]
    cr = view["cluster_run"]
    mats = {m["material_id"] for m in view["materials"]}
    if {a["material_id"] for a in cr["assignments"]} != mats:
        errs.append("materials != assignments")
    pev = {p["evidence_id"]: p for p in view["pair_evidence"]}
    for e in cr["edges"]:
        if e["pair_evidence_id"] not in pev:
            errs.append(f"krawędź bez pair_evidence: {e['pair_evidence_id']}")
    for p in pev.values():
        if not set(p["pair"]) <= mats:
            errs.append(f"para spoza materiałów: {p['evidence_id']}")
        if f"/runs/{cr['run_id']}/evidence/{p['evidence_id']}" not in p["evidence_url"]:
            errs.append(f"evidence_url niezgodny: {p['evidence_id']}")
    groups: dict[str, set] = {}
    for a in cr["assignments"]:
        for c in a["cluster_ids"]:
            groups.setdefault(c, set()).add(a["material_id"])
    if {c["cluster_id"]: set(c["material_ids"]) for c in view["clusters"]} != groups:
        errs.append("clusters != assignments")
    s = view["summary"]
    counts = {k: sum(1 for a in cr["assignments"] if a["status"] == k) for k in ("grouped", "singleton", "noise", "insufficient_data")}
    if any(s[k] != n for k, n in counts.items()) or s["material_count"] != len(cr["assignments"]):
        errs.append("summary != assignments")
    return errs


def check_body(spec: dict, schema: dict, body, label: str) -> list[str]:
    errs = [f"{label}: {e.message} @ {'/'.join(map(str, e.absolute_path))}" for e in schema_validator(spec, schema).iter_errors(body)]
    errs += [f"{label}: {x}" for x in leaks(body)]
    if isinstance(body, dict):
        view = body.get("result") if "result" in body else body if "cluster_run" in body else None
        if view:
            errs += [f"{label}: {x}" for x in check_cluster_run_view(view)]
    return errs


def iter_examples(spec: dict):
    """(operacja, status/'request', nazwa, ścieżka pliku, schemat) dla każdego `externalValue`; $ref do components.responses rozwijane."""
    comp = spec["components"]
    for path, item in spec["paths"].items():
        for method, op in item.items():
            if method not in ("get", "post"):
                continue
            rb = op.get("requestBody", {}).get("content", {})
            for mt, c in rb.items():
                for name, ex in c.get("examples", {}).items():
                    yield f"{method.upper()} {path}", "request", name, HERE / ex["externalValue"], c["schema"], mt
            for status, resp in op["responses"].items():
                if "$ref" in resp:
                    resp = comp["responses"][resp["$ref"].rsplit("/", 1)[1]]
                for mt, c in resp.get("content", {}).items():
                    for name, ex in c.get("examples", {}).items():
                        yield f"{method.upper()} {path}", status, name, HERE / ex["externalValue"], c["schema"], mt


def check_static() -> list[str]:
    from openapi_spec_validator import validate
    spec = load_spec()
    errs: list[str] = []
    try:
        validate(spec)
    except Exception as e:  # noqa: BLE001
        errs.append(f"specyfikacja OpenAPI: {e}")
    used = set()
    for op, status, name, file, schema, mt in iter_examples(spec):
        used.add(file.name)
        if not file.exists():
            errs.append(f"{op} {status} {name}: brak pliku {file.name}")
            continue
        errs += check_body(spec, schema, json.loads(file.read_text(encoding="utf-8")), f"{op} {status} {name}")
    for f in (HERE / "examples").glob("*.json"):
        if f.name not in used:
            errs.append(f"przykład {f.name} nie jest podpięty w openapi.yaml")
    return errs


# ------------------------------------------------------------------ żywy backend

def _call(base: str, method: str, path: str, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base.rstrip("/") + path, data=data, method=method, headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def _schema_for(spec: dict, op: str, status: int):
    path, method = op.split(" ", 1)[1], op.split(" ", 1)[0].lower()
    resp = spec["paths"][path][method]["responses"][str(status)]
    if "$ref" in resp:
        resp = spec["components"]["responses"][resp["$ref"].rsplit("/", 1)[1]]
    return resp["content"]["application/json"]["schema"]


def check_live(base: str, timeout_s: float = 120) -> list[str]:
    spec = load_spec()
    errs: list[str] = []

    def check(op: str, tmpl: str, status: int, body, expect: tuple[int, ...]):
        if status not in expect:
            errs.append(f"{op}: status {status}, oczekiwano {expect}")
            return
        if str(status) not in spec["paths"][tmpl][op.split()[0].lower()]["responses"]:
            errs.append(f"{op}: status {status} nieopisany w kontrakcie")
            return
        errs.extend(check_body(spec, _schema_for(spec, f"{op.split()[0]} {tmpl}", status), body, f"{op} -> {status}"))

    s, b = _call(base, "GET", "/api/v1/health"); check("GET /api/v1/health", "/api/v1/health", s, b, (200, 503))
    s, demo = _call(base, "GET", "/api/v1/demo"); check("GET /api/v1/demo", "/api/v1/demo", s, demo, (200,))
    if errs:
        return errs
    s, acc = _call(base, "POST", "/api/v1/runs", {"observation_id": demo["observation_id"]}); check("POST /api/v1/runs", "/api/v1/runs", s, acc, (202,))
    if errs:
        return errs
    rid, deadline, run = acc["run_id"], time.time() + timeout_s, None
    while time.time() < deadline:
        s, run = _call(base, "GET", f"/api/v1/runs/{rid}"); check("GET /api/v1/runs/{run_id}", "/api/v1/runs/{run_id}", s, run, (200,))
        if errs or run["status"] in ("succeeded", "failed"):
            break
        time.sleep(1)
    if run and run["status"] == "succeeded":
        for p in run["result"]["pair_evidence"]:
            s, ev = _call(base, "GET", p["evidence_url"])
            check("GET /api/v1/runs/{run_id}/evidence/{evidence_id}", "/api/v1/runs/{run_id}/evidence/{evidence_id}", s, ev, (200,))
    s, e = _call(base, "GET", "/api/v1/runs/run-nie-istnieje"); check("GET /api/v1/runs/{run_id}", "/api/v1/runs/{run_id}", s, e, (404,))
    s, e = _call(base, "POST", "/api/v1/runs", {"observation_id": "obs-nie-istnieje"}); check("POST /api/v1/runs", "/api/v1/runs", s, e, (404,))
    s, e = _call(base, "POST", "/api/v1/observations", {"url": "nie-adres"}); check("POST /api/v1/observations", "/api/v1/observations", s, e, (422,))
    return errs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url")
    a = ap.parse_args()
    errs = check_static() + (check_live(a.base_url) if a.base_url else [])
    for e in errs:
        print("BŁĄD:", e)
    print("OK" if not errs else f"{len(errs)} błędów")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
