"""Manifest i inwentarz zbudowane z magazynu (deterministyczne: bez znaczników czasu), kontrola z systemem plików."""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from ..contracts.models import ArtifactRole
from .store import Store, _atomic_write

KINDS = ("page_capture", "archive_capture", "loose_file", "url_observation")


def build_manifest(store: Store) -> tuple[dict, list[dict], list[dict]]:
    obs = sorted(store.observations(), key=lambda o: o.source_ref)
    inventory: list[dict] = []
    by_canon: dict[str, list] = defaultdict(list)
    sha_obs: dict[str, set[str]] = defaultdict(set)
    sha_occ: dict[str, int] = defaultdict(int)
    for o in obs:
        by_canon[o.canonical_id].append(o)
        for a in o.artifacts:
            inventory.append({"artifact_id": a.artifact_id, "observation_id": o.observation_id, "source_ref": o.source_ref,
                              "role": a.role.value, "sha256": a.sha256, "size": a.size, "media_type": a.media_type,
                              "origin": a.origin, "parent": a.parent.artifact_id if a.parent else None,
                              "local_ref": a.local_ref})
            if a.role is not ArtifactRole.input_url:
                sha_obs[a.sha256].add(o.observation_id)
                sha_occ[a.sha256] += 1
    kind_of = lambda o: next((t for t in o.tags if t in KINDS), "other")
    aliases = [{"canonical_id": c, "kind": kind_of(v[0]), "observation_ids": sorted(x.observation_id for x in v),
                "source_refs": sorted(x.source_ref for x in v)}
               for c, v in sorted(by_canon.items()) if len(v) > 1]
    shared = [{"sha256": s, "occurrences": sha_occ[s], "observations": len(sha_obs[s])}
              for s in sorted(sha_obs) if len(sha_obs[s]) > 1]
    counts = {
        "observations": len(obs),
        **{k: sum(1 for o in obs if kind_of(o) == k) for k in KINDS},
        "excluded_from_corpus": sum(1 for o in obs if "excluded_from_corpus" in o.tags),
        "artifact_occurrences": len(inventory),
        "unique_blobs": len(sha_obs),
        "canonical_groups": len(by_canon),
        "alias_groups": len(aliases),
        "page_capture_unique_canonical": len({o.canonical_id for o in obs if kind_of(o) == "page_capture"}),
        "shared_blobs": len(shared),
    }
    manifest = {
        "manifest_version": 1, "counts": counts,
        "observations": [{"observation_id": o.observation_id, "canonical_id": o.canonical_id, "source_ref": o.source_ref,
                          "input_kind": o.input_kind.value, "tags": o.tags, "capture_time": o.capture_time,
                          "version": len(store.versions(o.observation_id)), "artifacts": len(o.artifacts)} for o in obs],
        "aliases": aliases, "shared_blobs": shared,
    }
    inventory.sort(key=lambda r: (r["source_ref"], r["origin"] or "", r["artifact_id"]))
    return manifest, inventory, [o.model_dump(mode="json") for o in obs]


def write_manifest(store: Store) -> dict:
    manifest, inventory, obs = build_manifest(store)
    dump = lambda rows: ("\n".join(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in rows) + "\n").encode("utf-8")
    _atomic_write(store.root / "manifest.json", json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=1).encode("utf-8"))
    _atomic_write(store.root / "inventory.jsonl", dump(inventory))
    _atomic_write(store.root / "observations.jsonl", dump(obs))
    return manifest


def verify_blobs(store: Store) -> list[str]:
    """Każdy artefakt plikowy ma blob o zgodnej sumie i rozmiarze."""
    import hashlib
    bad = []
    for o in store.observations():
        for a in o.artifacts:
            if a.local_ref is None:
                continue
            p = store.root / a.local_ref
            if not p.is_file():
                bad.append(f"brak blobu {a.local_ref} ({o.source_ref}:{a.origin})")
            elif hashlib.sha256(p.read_bytes()).hexdigest() != a.sha256:
                bad.append(f"zła suma {a.local_ref}")
    return bad
