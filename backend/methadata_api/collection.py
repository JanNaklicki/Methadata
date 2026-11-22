"""Trwała kolekcja i publikowanie kolejnych migawek grafu."""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from itertools import combinations
from pathlib import Path
from threading import Lock

from methadata.contracts import PairEvidence, Relation, discover, ids
from methadata.contracts.models import ArtifactRef, InputKind, Observation
from methadata.experiments.perceptual import PerceptualExtractor
from methadata.ingest.detect import sniff
from methadata.ingest.store import now_iso
from methadata.runner import save_run

from .collection_archive import archive_groups, build_loose_observation, build_observation, build_url_observation, read_zip
from .engine import fusion_run
from .enrichment import enrich_domains
from .errors import safe_text
from .reference_visual import match_reference_figures
from .storage import Storage, atomic_write_json, read_json
from .summarizer import summarize_clusters
from .views import build_view, ensure_preview

log = logging.getLogger(__name__)
ANALYSABLE = {"landing_html", "landing_dom", "landing_screenshot", "creative_image", "resource", "har", "audio", "video", "document"}


def is_analysable(observation) -> bool:
    """Czy materiał ma choć jeden ślad, który którykolwiek ekstraktor potrafi odczytać (zwykły plik tekstowy nie)."""
    for artifact in observation.artifacts:
        role, media = artifact.role.value, (artifact.media_type or "").lower()
        if role not in ANALYSABLE or (role == "resource" and not media.startswith("image/")):
            continue
        if role == "document" and media != "application/pdf":
            continue
        return True
    return False


SUPPLEMENTAL_EXTRACTORS = (
    "perceptual-extractor", "url-har-extractor", "extract-code", "video-audio-extractor",
    "extract-documents", "people-entities-extractor", "narrative-slots-extractor",
    "source-contact-extractor", "source-affiliate-extractor",
)
SUPPLEMENTAL_METHODS = (
    "perceptual-phash-v1", "url-har-v1", "near-copy-jaccard-v1",
    "video-frames-v1", "audio-fingerprint-v1", "transcript-ngram-v1", "voice-aux-v1",
    "people-text-mention-v1", "people-text-persons-v1", "narrative-slots-v1",
    "source-contact-compare", "source-affiliate-compare",
)
METHOD_LABELS = {
    "perceptual-phash-v1": "Podobne grafiki (pHash)", "perceptual-keypoints-v1": "Wspólne punkty grafiki (ORB)",
    "url-har-v1": "Adresy i przekierowania (HAR)", "near-copy-jaccard-v1": "Wspólne fragmenty tekstu",
    "ocr-sentences-v1": "Tekst odczytany z obrazów (OCR)", "semantic-minilm-v1": "Podobna narracja (MiniLM)",
    "visual-embedding-v1": "Podobny wygląd (OpenCLIP)", "shared-script-v1": "Identyczny skrypt",
    "video-frames-v1": "Podobne klatki wideo (pHash)",
    "audio-fingerprint-v1": "Wspólna ścieżka dźwiękowa",
    "transcript-ngram-v1": "Wspólna transkrypcja (Whisper)",
    "voice-aux-v1": "Podobny głos mówcy (SpeechBrain)",
    "people-text-mention-v1": "Wspólne osoby i marki",
    "people-text-persons-v1": "Wspólne osoby publiczne",
    "narrative-slots-v1": "Podobny schemat narracji",
    "source-contact-compare": "Wspólne dane kontaktowe",
    "source-affiliate-compare": "Wspólne kody afiliacyjne",
}
MODEL_EXTRACTORS = ("ocr-layout-extractor", "visual-embedding-extractor")
MODEL_METHODS = ("perceptual-keypoints-v1", "ocr-sentences-v1", "semantic-minilm-v1", "visual-embedding-v1")

def capture_identity(observation):
    """Keep existing capture IDs when previously skipped nested members are recovered."""
    primary, nested = [], []
    for artifact in observation.artifacts:
        role = artifact.role.value
        if role == "resource" and (artifact.media_type or "").startswith("image/"):
            role = "creative_image"
        elif role == "landing_dom":
            role = "landing_html"
        pair = (role, artifact.sha256)
        (nested if (artifact.origin or "").startswith("ZIP!/") and artifact.origin.count("!/") > 1 else primary).append(pair)
    try:
        return ids.canonical_id(primary)
    except ValueError:
        return ids.canonical_id(nested, url=observation.canonical_id)


ACTIVE_SOURCES = """
    SELECT m.id, m.label, COALESCE(s.observation_id, m.observation_id)
    FROM materials m JOIN sources s ON s.material_id=m.id
    JOIN uploads u ON u.id=s.upload_id
    WHERE NOT m.archived AND m.analysable AND NOT u.archived
    ORDER BY m.id, s.rowid
"""


def combine_observations(store, observations):
    """Use every distinct artifact from active copies of a deduplicated capture."""
    base, *additional = observations
    chosen = dict.fromkeys((a.role, a.sha256) for a in base.artifacts)
    groups = [(base, base.artifacts)]
    for observation in additional:
        by_id = {a.artifact_id: a for a in observation.artifacts}
        included = {}

        def include(artifact):
            if artifact.parent and artifact.parent.artifact_id not in included:
                include(by_id[artifact.parent.artifact_id])
            included[artifact.artifact_id] = artifact

        for artifact in observation.artifacts:
            fingerprint = (artifact.role, artifact.sha256)
            if artifact.role.value != "archive" and fingerprint not in chosen:
                include(artifact)
                chosen[fingerprint] = None
        if included:
            groups.append((observation, list(included.values())))
    if len(groups) == 1:
        return base
    source = "merged:" + ids.sha256_bytes("|".join(obs.observation_id for obs, _ in groups).encode())
    oid = ids.observation_id(source, base.input_kind.value)
    combined = []
    for observation, artifacts in groups:
        mapping = {
            a.artifact_id: ids.artifact_id(oid, a.role.value, a.sha256, f"{observation.observation_id}:{a.artifact_id}")
            for a in artifacts
        }
        for artifact in artifacts:
            parent = artifact.parent
            if parent:
                parent = ArtifactRef(artifact_id=mapping[parent.artifact_id], locator=parent.locator)
            combined.append(artifact.model_copy(update={
                "artifact_id": mapping[artifact.artifact_id], "observation_id": oid, "parent": parent,
            }))
    merged = base.model_copy(update={"observation_id": oid, "source_ref": source, "artifacts": combined})
    store.save_observation(merged, "collection-merge")
    return merged


def _image_previews(bundle, store, previews_dir, hashes):
    """Podgląd dla każdego współdzielonego obrazu (hash treści -> opis podglądu); brak obrazu w magazynie = brak wpisu."""
    wanted = set(hashes)
    found = {}
    for observation in bundle.observations:
        for artifact in observation.artifacts:
            if artifact.sha256 in wanted and artifact.sha256 not in found and artifact.local_ref is not None:
                preview = ensure_preview(store, artifact, f"Wspólna grafika {artifact.sha256[:8]}", previews_dir)
                if preview:
                    found[artifact.sha256] = preview
    return found


def investigation_metadata(bundle, accepted, store=None, previews_dir=None, cache_dir: Path | None = None):
    material_ids = {o.observation_id: o.canonical_id for o in bundle.observations}
    artifacts = defaultdict(set)
    domains = defaultdict(set)
    texts = defaultdict(list)
    scores = defaultdict(dict)
    for pair in bundle.pairs:
        if pair.pair_evidence_id in accepted or pair.score is None:
            continue
        scores[pair.pair][pair.method_id] = pair.score
    families = bundle.runs[0].config.get("fusion", {}).get("families", {})
    edge_families = {
        pair.pair_evidence_id: [name for name, cfg in families.items()
                                if scores[pair.pair].get(cfg["method_id"], -1) >= cfg["support_min"]]
        for pair in bundle.pairs if pair.pair_evidence_id in accepted
    }
    for feature in bundle.features:
        if not feature.evidence_refs:
            continue
        mid = material_ids.get(feature.observation_id)
        if not mid:
            continue
        key = (feature.family, feature.kind)
        if key == ("lexical", "text_field") and isinstance(feature.value, dict):
            text = safe_text(str(feature.value.get("text", "")), 300)
            if len(text) >= 3 and text not in texts[mid]:
                texts[mid].append(text)
        if key in {("url_har", "host_order"), ("url_har", "url_parts")}:
            hosts = feature.value if feature.kind == "host_order" else [feature.value["host"]]
            for host in hosts:
                host = safe_text(host, 253)
                if host:
                    artifacts["domain", host].add(mid)
                    domains[mid].add(host)
        elif key == ("url_har", "integration_id"):
            label = safe_text(f"{feature.value['vendor']} · {feature.value['id']}", 150)
            artifacts["tracker", label].add(mid)
        elif key in {("asset", "image_sha256"), ("js", "source_sha256")}:
            artifacts["image" if key[0] == "asset" else "script", feature.value].add(mid)
        elif feature.family == "entity" and isinstance(feature.value, dict):
            name = safe_text(str(feature.value.get("canonical") or feature.value.get("name") or ""), 120)
            if name:
                art_kind = "person" if "person" in feature.kind else "brand"
                artifacts[art_kind, name].add(mid)
        elif feature.family == "contact":
            if isinstance(feature.value, dict):
                val = safe_text(str(feature.value.get("masked") or feature.value.get("hash") or feature.value.get("kind") or ""), 120)
            else:
                val = safe_text(str(feature.value), 120)
            if val:
                artifacts["contact", val].add(mid)
        elif feature.family == "affiliate" and isinstance(feature.value, dict):
            val = safe_text(str(feature.value.get("code") or feature.value.get("value") or feature.value.get("id") or ""), 120)
            if val:
                artifacts["affiliate", val].add(mid)

    # Wizualne dopasowania osób referencyjnych (var/reference_figures/)
    for mid, ref_hits in match_reference_figures(bundle).items():
        for hit in ref_hits:
            artifacts["person", f"{hit['person']} (wizualnie)"].add(mid)

    # Publiczne wzbogacanie infrastruktury domen (DNS/TLS/RDAP z cache)
    all_hosts = {h for hs in domains.values() for h in hs}
    domain_meta = enrich_domains(all_hosts, cache_dir / "domain_enrichment.json") if cache_dir else {}
    for mid, host_set in domains.items():
        for h in host_set:
            meta = domain_meta.get(h)
            if not meta:
                continue
            for ip in meta.get("ips", ())[:2]:
                artifacts["infrastructure", f"IP {ip}"].add(mid)
            if meta.get("registrar"):
                artifacts["infrastructure", f"Rejestrator: {meta['registrar']}"].add(mid)

    shared = [(kind, label, ids_) for (kind, label), ids_ in artifacts.items() if len(ids_) > 1]
    shared.sort(key=lambda item: (-len(item[2]), item[0], item[1]))
    previews = _image_previews(bundle, store, previews_dir, [label for kind, label, _ in shared if kind == "image"]) \
        if store is not None and previews_dir is not None else {}
    accepted_pairs = {tuple(sorted(pair.pair)) for pair in bundle.pairs if pair.pair_evidence_id in accepted}
    best_suggested: dict[tuple[str, str], object] = {}
    for pair in bundle.pairs:
        if pair.pair_evidence_id in accepted or pair.score is None or pair.score < 0.35:
            continue
        if pair.method_id != "fusion-v1" and pair.method_id not in METHOD_LABELS:
            continue
        key = tuple(sorted(pair.pair))
        if key in accepted_pairs:
            continue
        prev = best_suggested.get(key)
        # Prefer fusion-v1 or highest score per unordered material pair.
        if prev is None or (pair.method_id == "fusion-v1" and prev.method_id != "fusion-v1") or (
            (pair.method_id == "fusion-v1") == (prev.method_id == "fusion-v1") and pair.score > prev.score
        ):
            best_suggested[key] = pair
    suggested_edges = [
        {
            "source": pair.pair[0],
            "target": pair.pair[1],
            "pair_evidence_id": pair.pair_evidence_id,
            "score": pair.score,
            "method_id": pair.method_id,
            "label": METHOD_LABELS.get(pair.method_id, "Kandydat fuzji"),
            "explanation": safe_text(pair.explanation, 500),
        }
        for pair in sorted(best_suggested.values(), key=lambda item: (-item.score, item.pair_evidence_id))[:40]
    ]
    return {
        "artifacts": [
            {"kind": kind, "label": label, "material_ids": sorted(ids_),
             **({"preview": previews[label]} if kind == "image" and label in previews else {})}
            for kind, label, ids_ in shared
        ],
        "artifact_previews": store is not None,
        # Wyszukiwanie po treści (słowa kluczowe, nazwa produktu): zwięzły, małymi literami, ograniczony indeks na materiał.
        "search_text": {mid: " ".join(parts)[:1500].lower() for mid, parts in texts.items()},
        "domains": {mid: sorted(hosts) for mid, hosts in domains.items()},
        "domain_meta": domain_meta,
        "captured_at": {o.canonical_id: o.capture_time for o in bundle.observations if o.capture_time},
        "edge_families": edge_families,
        "suggested_edges": suggested_edges,
    }


class Collection:
    def __init__(self, storage: Storage, runs):
        self.storage = storage
        self.runs = runs
        self.writer = Lock()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="collection")

    def connect(self):
        return sqlite3.connect(self.storage.settings.meta_dir / "collection.sqlite3", timeout=300)

    def prepare(self):
        with closing(self.connect()) as db, db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS imports (
                    id TEXT PRIMARY KEY, report TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS materials (
                    id TEXT PRIMARY KEY, observation_id TEXT NOT NULL, label TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sources (
                    import_id TEXT NOT NULL, material_id TEXT NOT NULL, label TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS snapshots (
                    version INTEGER PRIMARY KEY, run_id TEXT NOT NULL UNIQUE
                );
            """)
            db.executescript("""
                CREATE TABLE IF NOT EXISTS uploads (
                    id TEXT PRIMARY KEY, import_id TEXT, filename TEXT NOT NULL,
                    label TEXT NOT NULL, created_at TEXT NOT NULL, sha TEXT, size INTEGER,
                    kind TEXT, archived INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS upload_members (
                    id TEXT PRIMARY KEY, upload_id TEXT NOT NULL, path TEXT NOT NULL,
                    sha TEXT NOT NULL, size INTEGER NOT NULL, material_id TEXT
                );
                CREATE INDEX IF NOT EXISTS upload_members_by_upload ON upload_members(upload_id);
                CREATE INDEX IF NOT EXISTS uploads_by_import ON uploads(import_id);
                CREATE TABLE IF NOT EXISTS observation_receipts (
                    profile TEXT NOT NULL, observation_id TEXT NOT NULL, version INTEGER NOT NULL,
                    PRIMARY KEY (profile, observation_id, version)
                );
                CREATE INDEX IF NOT EXISTS sources_by_material ON sources(material_id);
                CREATE TABLE IF NOT EXISTS manual_edges (
                    pair_key TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    target TEXT NOT NULL,
                    evidence_id TEXT NOT NULL,
                    score REAL NOT NULL,
                    label TEXT NOT NULL,
                    explanation TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
            """)
            for table, column, definition in (
                ("materials", "archived", "INTEGER NOT NULL DEFAULT 0"),
                ("materials", "analysable", "INTEGER NOT NULL DEFAULT 1"),
                ("sources", "upload_id", "TEXT"),
                ("sources", "observation_id", "TEXT"),
            ):
                if column not in {row[1] for row in db.execute(f"PRAGMA table_info({table})")}:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            self._migrate(db)
            pending = []
            for (data,) in db.execute("SELECT report FROM imports").fetchall():
                report = json.loads(data)
                if report["status"] not in ("queued", "running"):
                    continue
                if report.get("upload_ids"):
                    report.update(status="queued", stage="Oczekiwanie na import")
                    pending.append(report["id"])
                else:
                    report.update(status="failed", error="Przerwany import nie ma zapisanego oryginału.")
                self._report(db, report)
        for import_id in pending:
            self.schedule(import_id)

    def shutdown(self):
        self.pool.shutdown(wait=True)

    def _migrate(self, db):
        store = self.storage.store()
        for row, import_id, mid, name, created, oid in db.execute("""
            SELECT s.rowid, s.import_id, s.material_id, s.label, s.created_at, m.observation_id
            FROM sources s JOIN materials m ON m.id = s.material_id WHERE s.upload_id IS NULL
        """).fetchall():
            upload_id = f"legacy-source-{row}"
            arts = store.observation(oid).artifacts if store.versions(oid) else []
            # Old collection ZIPs were rebuilt before ingestion. Their bytes are not the original.
            original = next((a for a in arts if not a.parent and a.role.value != "archive" and store.has_blob(a.sha256)), None)
            db.execute("""INSERT OR IGNORE INTO uploads
                (id, import_id, filename, label, created_at, sha, size, kind)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (upload_id, import_id, name, name, created, original.sha256 if original else None,
                 original.size if original else None, None))
            db.execute("UPDATE sources SET upload_id=? WHERE rowid=?", (upload_id, row))
            for artifact in arts:
                if artifact.parent and artifact.local_ref and store.has_blob(artifact.sha256):
                    self._member(db, upload_id, artifact.origin or artifact.artifact_id,
                                 artifact.sha256, artifact.size, mid)
        for profile in ("demo", "jury"):
            selected = self.runs.storage.for_profile(profile)
            if selected is None or not selected.settings.store_dir.is_dir():
                continue
            old_store = selected.store()
            index = selected.index() or {}
            seeds = {*index.get("observation_ids", []), index.get("target_observation_id")}
            labels = selected.labels()
            for oid in old_store.observation_ids():
                if oid in seeds:
                    continue
                for version in old_store.versions(oid):
                    upload_id = f"legacy-{profile}-{oid}-{version['version']}"
                    if db.execute("SELECT 1 FROM uploads WHERE id=?", (upload_id,)).fetchone() or db.execute(
                        "SELECT 1 FROM observation_receipts WHERE profile=? AND observation_id=? AND version=?",
                        (profile, oid, version["version"]),
                    ).fetchone():
                        continue
                    obs = Observation.model_validate(version["observation"])
                    if obs.input_kind == InputKind.url_only:
                        continue
                    original = next((a for a in obs.artifacts if a.local_ref and not a.parent), None)
                    if not original:
                        continue
                    filename = Path(original.origin or obs.source_ref).name
                    sha, size, kind = None, None, None
                    if old_store.has_blob(original.sha256):
                        if db.execute("SELECT 1 FROM uploads WHERE sha=? AND filename=? AND import_id IS NOT NULL",
                                      (original.sha256, filename)).fetchone():
                            continue
                        with old_store.blob_path(original.sha256).open("rb") as data:
                            sha, size, head, _ = store.put_stream(data)
                        kind = sniff(head, filename, size).kind
                    label = labels.get(oid, filename)
                    db.execute("""INSERT INTO uploads
                        (id, filename, label, created_at, sha, size, kind) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (upload_id, filename, label, version["imported_at"], sha, size, kind))

    def remember_observation(self, profile, observation_id, version):
        with closing(self.connect()) as db, db:
            db.execute("INSERT OR IGNORE INTO observation_receipts VALUES (?, ?, ?)",
                       (profile, observation_id, version))

    def report(self, import_id: str):
        with closing(self.connect()) as db:
            row = db.execute("SELECT report FROM imports WHERE id=?", (import_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def current(self):
        with closing(self.connect()) as db, db:
            db.execute("BEGIN")
            row = db.execute("SELECT version, run_id FROM snapshots ORDER BY version DESC LIMIT 1").fetchone()
            recent = []
            for (data,) in db.execute("SELECT report FROM imports ORDER BY rowid DESC LIMIT 10"):
                report = json.loads(data)
                recent.append({key: report[key] for key in ("id", "created_at", "status")})
            if not row:
                return {"version": 0, "run_id": None, "graph": None, "sources": {}, "imports": recent}
            sources = defaultdict(list)
            for mid, label, date in db.execute("""
                SELECT s.material_id, s.label, s.created_at FROM sources s
                JOIN uploads u ON s.upload_id = u.id WHERE NOT u.archived ORDER BY s.rowid
            """):
                sources[mid].append({"label": label, "date": date})
        run_id = row[1] if not row[1].startswith("empty-") else None
        graph = self.runs.result(run_id) if run_id else None
        if run_id and graph is None:
            raise OSError("Nie można odczytać migawki kolekcji.")
        if graph:
            accepted = {e["pair_evidence_id"] for e in graph["cluster_run"]["edges"]}
            bundle = None
            investigation = graph.get("investigation") or {}
            if "suggested_edges" not in investigation or "domain_meta" not in investigation or not investigation.get("artifact_previews")\
                    or "search_text" not in investigation:
                bundle = self.runs.bundle(row[1])
                if bundle is not None:
                    graph["investigation"] = investigation_metadata(
                        bundle, accepted, self.storage.store(), self.storage.settings.previews_dir,
                        self.storage.settings.meta_dir)
                    try:  # starsze migawki dostają podglądy raz, nie przy każdym odczycie
                        atomic_write_json(self.runs.run_dir(run_id) / "result.json", graph)
                    except OSError:
                        logging.getLogger(__name__).warning("Nie zapisano podglądów śladów dla %s", run_id, exc_info=True)
            manual_ids = self._apply_manual_edges(graph, run_id)
            if manual_ids:
                accepted = {e["pair_evidence_id"] for e in graph["cluster_run"]["edges"]}
            graph.setdefault("investigation", {})["manual_edge_ids"] = sorted(manual_ids)
            if graph.get("clusters"):
                if bundle is None:
                    bundle = self.runs.bundle(row[1])
                summarize_clusters(bundle, graph, self.storage.settings.meta_dir / "cluster_summaries.json")
            graph["pair_evidence"] = [p for p in graph["pair_evidence"] if p["score"] is not None
                                      and p["evidence_id"] in accepted]
        return {"version": row[0], "run_id": run_id, "graph": graph, "sources": sources, "imports": recent}

    def promote_edge(self, run_id: str, evidence_id: str):
        """Zatwierdza sugerowane/dodatkowe powiązanie jako potwierdzoną krawędź w kolekcji."""
        bundle = self.runs.bundle(run_id)
        if bundle is None:
            return None
        pair = next((p for p in bundle.pairs if p.pair_evidence_id == evidence_id and p.score is not None), None)
        if pair is None:
            return None
        left, right = sorted(pair.pair)
        pair_key = f"{left}\0{right}"
        label = METHOD_LABELS.get(pair.method_id, "Zatwierdzony sygnał")
        with closing(self.connect()) as db, db:
            db.execute(
                """INSERT OR REPLACE INTO manual_edges
                   (pair_key, source, target, evidence_id, score, label, explanation, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (pair_key, left, right, pair.pair_evidence_id, float(pair.score), label,
                 safe_text(pair.explanation, 500), now_iso()),
            )
        return {"evidence_id": pair.pair_evidence_id, "pair": [left, right], "score": float(pair.score), "label": label}

    def unpromote_edge(self, evidence_id: str) -> bool:
        with closing(self.connect()) as db, db:
            cur = db.execute("DELETE FROM manual_edges WHERE evidence_id=?", (evidence_id,))
            return cur.rowcount > 0

    def _apply_manual_edges(self, graph: dict, run_id: str | None) -> set[str]:
        with closing(self.connect()) as db:
            rows = db.execute(
                "SELECT source, target, evidence_id, score, label, explanation FROM manual_edges ORDER BY rowid"
            ).fetchall()
        if not rows:
            return set()
        materials = {m["material_id"] for m in graph.get("materials", ())}
        edges = list(graph["cluster_run"].get("edges") or [])
        existing_pairs = {tuple(sorted((e["source"], e["target"]))) for e in edges}
        existing_ids = {e["pair_evidence_id"] for e in edges}
        pe_by_id = {p["evidence_id"]: p for p in graph.get("pair_evidence", ())}
        manual_ids: set[str] = set()
        added = False
        for src, dst, eid, score, label, explanation in rows:
            if src not in materials or dst not in materials:
                continue
            pkey = tuple(sorted((src, dst)))
            if pkey in existing_pairs or eid in existing_ids:
                continue
            edges.append({"source": src, "target": dst, "pair_evidence_id": eid, "weight": score})
            existing_pairs.add(pkey)
            existing_ids.add(eid)
            manual_ids.add(eid)
            added = True
            if eid not in pe_by_id:
                graph["pair_evidence"].append({
                    "evidence_id": eid,
                    "pair": [src, dst],
                    "method_id": "manual-promoted-v1",
                    "relation": "campaign_candidate",
                    "score": score,
                    "explanation": explanation,
                    "evidence_url": f"/api/v1/runs/{run_id}/evidence/{eid}" if run_id else "",
                })
            inv = graph.get("investigation")
            if inv is not None:
                inv.setdefault("edge_families", {})[eid] = ["text"]
        if not added:
            return manual_ids
        graph["cluster_run"]["edges"] = edges
        # Usuń zatwierdzone pary z listy sugerowanych krawędzi przerywanych.
        inv = graph.get("investigation")
        if inv and "suggested_edges" in inv:
            inv["suggested_edges"] = [
                se for se in inv["suggested_edges"]
                if tuple(sorted((se["source"], se["target"]))) not in existing_pairs
            ]
        # Przelicz spójne składowe (grupy) z uwzględnieniem ręcznie zatwierdzonych krawędzi.
        adj = defaultdict(set)
        for e in edges:
            adj[e["source"]].add(e["target"])
            adj[e["target"]].add(e["source"])
        visited: set[str] = set()
        new_clusters = []
        prior_summary = {frozenset(c["material_ids"]): c.get("summary") for c in graph.get("clusters", ())}
        for mid in sorted(materials):
            if mid in visited or not adj[mid]:
                continue
            comp = []
            stack = [mid]
            visited.add(mid)
            while stack:
                cur = stack.pop()
                comp.append(cur)
                for nxt in adj[cur]:
                    if nxt not in visited:
                        visited.add(nxt)
                        stack.append(nxt)
            comp.sort()
            cid = "cl-" + ids.sha256_bytes("|".join(comp).encode())[:12]
            new_clusters.append({
                "cluster_id": cid,
                "material_ids": comp,
                "relation": "campaign_candidate",
                "summary": prior_summary.get(frozenset(comp), ""),
            })
        graph["clusters"] = new_clusters
        grouped = {m for c in new_clusters for m in c["material_ids"]}
        for a in graph["cluster_run"].get("assignments", ()):
            if a["material_id"] in grouped:
                a["status"] = "grouped"
                a["cluster_ids"] = [c["cluster_id"] for c in new_clusters if a["material_id"] in c["material_ids"]]
        return manual_ids

    def signals(self, bundle, run_id, pair=None, material=None):
        return [
            {"evidence_id": p.pair_evidence_id, "pair": list(p.pair), "label": METHOD_LABELS[p.method_id],
             "score": p.score, "status": "missing" if p.score is None else "scored",
             "explanation": safe_text(p.explanation, 500)}
            for p in bundle.pairs
            if p.run_id == run_id and p.method_id in METHOD_LABELS
            and (p.pair == pair if pair else material in p.pair and p.score is not None and (p.score >= 0.5 or p.method_id == "shared-script-v1"))
        ]

    def _script_pairs(self, bundle, run_id):
        oid_to_mid = {o.observation_id: o.canonical_id for o in bundle.observations}
        scripts = defaultdict(lambda: defaultdict(list))
        for feature in bundle.features:
            if (feature.family, feature.kind) == ("js", "source_sha256") and feature.evidence_refs:
                scripts[oid_to_mid[feature.observation_id]][feature.value].extend(feature.evidence_refs)
        for left, right in combinations(sorted(scripts), 2):
            shared = sorted(scripts[left].keys() & scripts[right].keys())
            if shared:
                yield PairEvidence(
                    run_id=run_id, pair=(left, right), method_id="shared-script-v1",
                    relation=Relation.campaign_candidate, score=len(shared) / len(scripts[left].keys() | scripts[right].keys()),
                    explanation=f"Identyczne skrypty w obu materiałach: {len(shared)}. Porównano skróty SHA-256.",
                    evidence_refs={mid: list({
                        ref.model_dump_json(): ref for digest in shared for ref in scripts[mid][digest]
                    }.values()) for mid in (left, right)},
                )

    def _report(self, db, report):
        db.execute("""
            INSERT INTO imports VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET report=excluded.report
        """, (report["id"], json.dumps(report, ensure_ascii=False)))

    def _save_report(self, report):
        with closing(self.connect()) as db, db:
            self._report(db, report)

    def register(self, files):
        store = self.storage.store()
        report = {"id": uuid.uuid4().hex, "status": "queued", "entries": [], "issues": [], "merges": [],
                  "created_at": now_iso(), "run_id": None, "stage": "Oczekiwanie na import", "upload_ids": []}
        records = []
        for path, filename, kind, label in files:
            with path.open("rb") as data:
                sha, size, _, _ = store.put_stream(data)
            upload_id = uuid.uuid4().hex
            report["upload_ids"].append(upload_id)
            records.append((upload_id, report["id"], filename, label, report["created_at"], sha, size, kind))
        with closing(self.connect()) as db, db:
            db.executemany("""INSERT INTO uploads
                (id, import_id, filename, label, created_at, sha, size, kind) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", records)
            self._report(db, report)
        return report

    def register_urls(self, items):
        """Rejestruje jeden lub więcej adresów URL/domen jako import kolekcji (`kind='url'`)."""
        store = self.storage.store()
        report = {"id": uuid.uuid4().hex, "status": "queued", "entries": [], "issues": [], "merges": [],
                  "created_at": now_iso(), "run_id": None, "stage": "Oczekiwanie na import adresu", "upload_ids": []}
        records = []
        for url, label, fetch in items:
            payload = json.dumps({"url": url, "fetch": bool(fetch)}, ensure_ascii=False).encode("utf-8")
            sha, size, _ = store.put_bytes(payload)
            upload_id = uuid.uuid4().hex
            report["upload_ids"].append(upload_id)
            records.append((upload_id, report["id"], label, label, report["created_at"], sha, size, "url"))
        with closing(self.connect()) as db, db:
            db.executemany("""INSERT INTO uploads
                (id, import_id, filename, label, created_at, sha, size, kind) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", records)
            self._report(db, report)
        return report

    def schedule(self, import_id):
        self.pool.submit(self.process, import_id)

    def retry(self, import_id):
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT report FROM imports WHERE id=?", (import_id,)).fetchone()
            report = json.loads(row[0]) if row else None
            if not report or report["status"] != "failed" or not report.get("upload_ids"):
                return None
            report.update(status="queued", stage="Oczekiwanie na import")
            report.pop("error", None)
            self._report(db, report)
        self.schedule(import_id)
        return report

    def process(self, import_id):
        with self.writer:
            report = self.report(import_id)
            if report["status"] != "queued":
                return report
            report.update(status="running", stage="Odczyt plików", entries=[], issues=[], merges=[],
                          archive={"loaded": 0, "skipped": 0, "metadata": 0})
            self._save_report(report)
            try:
                self._process_files(report)
            except Exception:
                log.exception("Import kolekcji %s", import_id)
                report.update(status="failed", entries=[], merges=[], run_id=None, stage="Błąd analizy",
                              error="Nie udało się zakończyć analizy. Zapisane pliki można przetworzyć ponownie.")
                self._save_report(report)
            return report

    def _member(self, db, upload_id, path, sha, size, mid):
        member_id = ids.sha256_bytes(f"{upload_id}:{path}".encode())
        db.execute("INSERT OR REPLACE INTO upload_members VALUES (?, ?, ?, ?, ?, ?)",
                   (member_id, upload_id, path, sha, size, mid))

    def _process_files(self, report):
        store = self.storage.store()
        with closing(self.connect()) as db:
            files = db.execute("""SELECT id, filename, kind, sha, size FROM uploads
                                  WHERE import_id=? ORDER BY rowid""", (report["id"],)).fetchall()
        candidates = []
        for index, (upload_id, filename, kind, sha, size) in enumerate(files):
            report["stage"] = f"Odczyt materiałów ({index + 1}/{len(files)})"
            self._save_report(report)
            if kind == "zip":
                outer, members = read_zip(store, sha, size, filename, report["issues"], report["archive"])
                groups = [(name, build_observation(store, outer, entries, capture), [e for e, _ in entries], None)
                          for name, entries, capture in archive_groups(outer, members)]
            elif kind == "url":
                spec = json.loads(store.blob_path(sha).read_text(encoding="utf-8"))
                report["stage"] = f"Pobieranie {filename} ({index + 1}/{len(files)})" if spec.get("fetch", True) else f"Zapisywanie adresu {filename}"
                self._save_report(report)
                obs, note = build_url_observation(store, spec["url"], fetch=bool(spec.get("fetch", True)))
                groups = [(filename, obs, [], note)]
            else:
                groups = [(filename, build_loose_observation(store, filename, kind, sha, size), [], None)]
            if not groups:
                report["entries"].append({"label": filename, "status": "skipped", "reason": "Brak czytelnych plików."})
            with closing(self.connect()) as db, db:
                for name, obs, members, note in groups:
                    mid = capture_identity(obs)
                    store.save_observation(obs, report["id"])
                    for member in members:
                        self._member(db, upload_id, member.origin, member.sha, member.size, mid)
                    candidates.append((upload_id, safe_text(name, 200), mid, obs, note))
        report["stage"] = "Analiza materiałów i publikowanie grafu"
        self._save_report(report)
        with closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            before = self._active_materials(db)
            for upload_id, name, mid, obs, note in candidates:
                analysable = is_analysable(obs)
                prior = db.execute("SELECT observation_id FROM materials WHERE id=?", (mid,)).fetchone()
                if prior is None:
                    db.execute("INSERT INTO materials VALUES (?, ?, ?, 0, ?)", (mid, obs.observation_id, name, analysable))
                db.execute("""INSERT INTO sources (import_id, material_id, label, created_at, upload_id, observation_id)
                              VALUES (?, ?, ?, ?, ?, ?)""",
                           (report["id"], mid, name, report["created_at"], upload_id, obs.observation_id))
                entry = {"label": name, "status": "duplicate" if prior else "added", "material_id": mid}
                if note:
                    entry["reason"] = note
                report["entries"].append(entry)
            self._publish_if_changed(db, before, report)
            if not candidates and report["issues"]:
                report.update(status="failed", stage="Błąd odczytu",
                              error="Nie udało się odczytać plików z importu. Oryginał jest dostępny w bazie.")
            else:
                report.update(status="succeeded", stage="Zakończono")
            self._report(db, report)

    def _active_materials(self, db):
        store = self.storage.store()
        grouped = defaultdict(dict)
        for mid, label, oid in db.execute(ACTIVE_SOURCES):
            grouped[mid, label][oid] = None
        return [(mid, combine_observations(store, [store.observation(oid) for oid in ids_]).observation_id, label)
                for (mid, label), ids_ in grouped.items()]

    def _publish_if_changed(self, db, before, report):
        rows = self._active_materials(db)
        previous = db.execute("SELECT run_id FROM snapshots ORDER BY version DESC LIMIT 1").fetchone()
        if previous and previous[0].startswith("empty-"):
            previous = None
        report["run_id"] = previous[0] if previous else None
        if rows == before:
            return
        if not rows:
            db.execute("INSERT INTO snapshots(run_id) VALUES (?)", ("empty-" + uuid.uuid4().hex,))
            return
        self._publish(db, self.storage.store(), rows, previous, report)

    def _publish(self, db, store, rows, previous, report):
        observations = [store.observation(oid).model_copy(update={"canonical_id": mid}) for mid, oid, _ in rows]
        labels = {oid: label for _, oid, label in rows}
        run_id = "run-" + uuid.uuid4().hex[:16]
        adapters = discover(strict=False)
        extra_extractors = SUPPLEMENTAL_EXTRACTORS
        extra_methods = SUPPLEMENTAL_METHODS
        if self.storage.settings.collection_models:
            extra_extractors += MODEL_EXTRACTORS
            extra_methods += MODEL_METHODS
        elif "perceptual-extractor" in adapters:
            adapters["perceptual-extractor"] = PerceptualExtractor({"extract_keypoints": False})
        # A restore can reuse the last nonempty snapshot's feature extraction.
        cached = previous or db.execute(
            "SELECT run_id FROM snapshots WHERE run_id LIKE 'run-%' ORDER BY version DESC LIMIT 1"
        ).fetchone()
        previous_id = cached[0] if cached else None
        record, bundle = fusion_run(
            self.storage.settings, observations, store, adapters, run_id, run_id,
            extra_extractors=[name for name in extra_extractors if name in adapters],
            extra_methods=[name for name in extra_methods if name in adapters],
            previous=self.runs.bundle(previous_id) if previous_id else None,
            previous_record=read_json(self.runs.run_dir(previous_id) / "run.json") if previous_id else None,
        )
        bundle.pairs.extend(self._script_pairs(bundle, run_id))
        record["profile"] = "collection"
        record["enabled_signals"] = sorted({"shared-script-v1", *(name for name in extra_methods if name in adapters)})
        view = build_view(run_id, bundle, labels, store, self.runs.storage.settings.previews_dir)
        view["collection"] = True
        view["investigation"] = investigation_metadata(
            bundle, {e.pair_evidence_id for e in bundle.runs[0].edges}, store,
            self.runs.storage.settings.previews_dir, self.storage.settings.meta_dir)
        previous_view = self.runs.result(previous[0]) if previous else None
        for cluster in view["clusters"]:
            members = set(cluster["material_ids"])
            older = [c for c in (previous_view or {}).get("clusters", []) if members.issuperset(c["material_ids"])]
            if len(older) > 1:
                report["merges"].append({"material_ids": cluster["material_ids"], "previous": [c["material_ids"] for c in older],
                                         "cluster_id": cluster["cluster_id"]})
        run_dir = self.runs.run_dir(run_id)
        save_run({"record": record, "bundle": bundle}, run_dir)
        atomic_write_json(run_dir / "result.json", view)
        date = now_iso()
        atomic_write_json(run_dir / "state.json", {"run_id": run_id, "status": "succeeded", "profile": "collection",
                          "observation_id": observations[0].observation_id, "created_at": date, "updated_at": date})
        db.execute("INSERT INTO snapshots(run_id) VALUES (?)", (run_id,))
        report["run_id"] = run_id
        report["first_material"] = not previous and len(rows) == 1

    def uploads(self):
        with closing(self.connect()) as db, db:
            db.execute("BEGIN")
            db.row_factory = sqlite3.Row
            rows = db.execute("""
                SELECT u.*, i.report FROM uploads u LEFT JOIN imports i ON u.import_id=i.id
                ORDER BY u.created_at DESC, u.rowid DESC
            """).fetchall()
            materials = defaultdict(dict)
            for row in db.execute("""
                SELECT s.upload_id, m.id, m.label, m.archived, m.analysable, s.label AS source_label
                FROM sources s JOIN materials m ON m.id=s.material_id ORDER BY m.label, m.id
            """):
                material = materials[row["upload_id"]].setdefault(row["id"], {
                    "id": row["id"], "label": row["label"], "archived": bool(row["archived"]),
                    "analysable": bool(row["analysable"]), "source_labels": [],
                })
                if row["source_label"] not in material["source_labels"]:
                    material["source_labels"].append(row["source_label"])
            members = defaultdict(list)
            for row in db.execute("SELECT * FROM upload_members ORDER BY path"):
                members[row["upload_id"]].append({
                    "id": row["id"], "path": row["path"], "size": row["size"], "material_id": row["material_id"],
                })
        return [{
            "id": row["id"], "filename": row["filename"], "label": row["label"],
            "size": row["size"], "created_at": row["created_at"], "archived": bool(row["archived"]),
            "downloadable": bool(row["sha"]), "import_id": row["import_id"],
            "status": json.loads(row["report"])["status"] if row["report"] else "succeeded",
            "materials": list(materials[row["id"]].values()), "members": members[row["id"]],
        } for row in rows]

    def download(self, upload_id, member_id=None):
        with closing(self.connect()) as db:
            if member_id:
                row = db.execute("SELECT sha, path FROM upload_members WHERE upload_id=? AND id=?",
                                 (upload_id, member_id)).fetchone()
            else:
                row = db.execute("SELECT sha, filename FROM uploads WHERE id=?", (upload_id,)).fetchone()
        if not row or not row[0]:
            return None
        path = self.storage.store().blob_path(row[0])
        return (path, Path(row[1]).name) if path.is_file() else None

    def update(self, table, item_id, changes):
        if table not in ("uploads", "materials"):
            raise ValueError(table)
        with self.writer, closing(self.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            before = self._active_materials(db)
            row = db.execute(f"SELECT label, archived FROM {table} WHERE id=?", (item_id,)).fetchone()
            if not row:
                return None
            label = changes.get("label", row[0])
            archived = changes.get("archived", bool(row[1]))
            db.execute(f"UPDATE {table} SET label=?, archived=? WHERE id=?", (label, archived, item_id))
            self._publish_if_changed(db, before, {"merges": []})
        return {"id": item_id, "label": label, "archived": archived}
