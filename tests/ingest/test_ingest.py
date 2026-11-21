import json
import os
import shutil
import zipfile
from pathlib import Path

import pytest

from methadata.contracts import discover
from methadata.contracts.interfaces import AdapterKind
from methadata.ingest import FileImporter, Limits, Store, verify_blobs
from methadata.ingest.detect import sniff

DATA = Path(os.environ.get("METHADATA_BACKUP", Path(__file__).resolve().parents[2].parent.parent.parent / "BACKUP"))
URLS = DATA.parent / "scam_links.dec"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def make_page(d: Path, html="<!doctype html><html><head><title>x</title></head><body>a</body></html>", png=PNG):
    d.mkdir(parents=True)
    (d / "page.html").write_text(html)
    (d / "shot.png").write_bytes(png)
    (d / "page_files").mkdir()
    (d / "page_files" / "main.js.pobrane").write_text("(function(){window.x=1;document.title='a';})();")
    (d / "page_files" / "noext").write_bytes(PNG + b"1")
    return d


def run(store, *src):
    return FileImporter().import_sources([str(s) for s in src], Store(store))


def test_discover_returns_importer():
    assert "importer-files-v1" in discover(AdapterKind.importer)


def test_idempotent_and_alias(tmp_path):
    root = tmp_path / "in"
    make_page(root / "a")
    shutil.copytree(root / "a", root / "b")
    r1 = run(tmp_path / "s", root)
    assert r1.summary()["new"] == 1 and r1.summary()["duplicate_capture"] == 1
    m1 = (tmp_path / "s" / "manifest.json").read_bytes()
    r2 = run(tmp_path / "s", root)
    assert r2.count("unchanged") == 2 and r2.count("new") == 0
    assert (tmp_path / "s" / "manifest.json").read_bytes() == m1
    man = json.loads(m1)
    assert man["counts"]["page_capture"] == 2 and man["counts"]["canonical_groups"] == 1
    assert len(man["aliases"]) == 1 and man["counts"]["shared_blobs"] > 0
    assert verify_blobs(Store(tmp_path / "s")) == []


def test_renamed_directory_and_new_version(tmp_path):
    make_page(tmp_path / "in" / "a")
    run(tmp_path / "s", tmp_path / "in")
    (tmp_path / "in").rename(tmp_path / "renamed")  # zmiana nazwy katalogu głównego
    assert run(tmp_path / "s", tmp_path / "renamed").count("unchanged") == 1
    shutil.copytree(tmp_path / "renamed" / "a", tmp_path / "renamed" / "c")
    (tmp_path / "renamed" / "a" / "page.html").write_text("<html><body>changed</body></html>")
    rep = run(tmp_path / "s", tmp_path / "renamed")
    st = {e.source_ref: e.status for e in rep.entries}
    assert st["dir:a"] == "new_version" and st["dir:c"] == "duplicate_capture"
    st_ = Store(tmp_path / "s")
    assert len(st_.versions(rep.entries[0].observation_id)) >= 1


def test_zip_and_unpacked_copy_share_canonical(tmp_path):
    d = tmp_path / "in"
    d.mkdir()
    files = {"html/source.html": b"<html><body>x</body></html>", "screenshots/full.png": PNG,
             "network.har": b'{"log":{"version":"1.2","creator":{},"entries":[]}}', "archive_info.json": b'{"title":"x | 502: Bad gateway","timestamp_utc":"2026-10-08T09:09:43.4"}'}
    with zipfile.ZipFile(d / "a.zip", "w") as z:
        for n, b in files.items():
            z.writestr(n, b)
    for n, b in files.items():
        (d / "arch" / Path(n)).parent.mkdir(parents=True, exist_ok=True)
        (d / "arch" / n).write_bytes(b)
    rep = run(tmp_path / "s", d)
    ents = {e.source_ref: e for e in rep.entries}
    assert ents["zip:a.zip"].canonical_id == ents["dir:arch"].canonical_id
    assert "excluded_from_corpus" in ents["zip:a.zip"].tags and "http_502" in ents["dir:arch"].tags
    obs = Store(tmp_path / "s").observation(ents["zip:a.zip"].observation_id)
    members = [a for a in obs.artifacts if a.parent_relation and a.parent_relation.value == "archive_member"]
    assert len(members) == 4 and all(a.origin.startswith("a.zip!/") for a in members)
    assert obs.capture_time == "2026-10-08T09:09:43.4Z"


def test_zip_hostile_entries(tmp_path):
    d = tmp_path / "in"
    d.mkdir()
    with zipfile.ZipFile(d / "bad.zip", "w") as z:
        z.writestr("../evil.txt", "x")
        z.writestr("/abs.txt", "x")
        z.writestr("C:/win.txt", "x")
        z.writestr("ok/a.txt", "fine")
        z.writestr("big.bin", b"0" * 5000)
    rep = FileImporter(Limits(max_member_bytes=1000)).import_sources([d], Store(tmp_path / "s"))
    codes = [(i.code, i.origin) for i in rep.issues]
    assert sum(c == "archive_entry_rejected" for c, _ in codes) == 4
    obs = Store(tmp_path / "s").observation(rep.entries[0].observation_id)
    assert sorted(a.origin for a in obs.artifacts) == ["bad.zip", "bad.zip!/ok/a.txt"]
    assert not (tmp_path / "evil.txt").exists()


def test_zip_total_limit_and_entries_limit(tmp_path):
    d = tmp_path / "in"
    d.mkdir()
    with zipfile.ZipFile(d / "z.zip", "w") as z:
        for i in range(10):
            z.writestr(f"f{i}.txt", f"{i}" * 100)
    rep = FileImporter(Limits(max_entries=5)).import_sources([d], Store(tmp_path / "s"))
    assert any(i.code == "archive_rejected" for i in rep.issues)
    rep = FileImporter(Limits(max_total_bytes=250)).import_sources([d], Store(tmp_path / "s2"))
    assert sum(i.code == "archive_entry_rejected" for i in rep.issues) >= 7


def test_partial_failure_corrupt_zip_and_unknown(tmp_path):
    d = tmp_path / "in"
    make_page(d / "ok")
    (d / "bad.zip").write_bytes(b"PK\x03\x04garbage")
    (d / "blob.bin").write_bytes(bytes(range(256)) * 4)
    (d / "missing_ref").mkdir()
    rep = run(tmp_path / "s", d, tmp_path / "nie_ma")
    codes = {i.code for i in rep.issues}
    assert "source_missing" in codes and "unknown_format" in codes
    assert any(e.source_ref == "dir:ok" for e in rep.entries)
    assert any(e.source_ref == "file:blob.bin" for e in rep.entries)


@pytest.mark.skipif(os.name == "nt" or os.geteuid() == 0, reason="wymaga uprawnień")
def test_unreadable_file_is_partial_error(tmp_path):
    p = make_page(tmp_path / "in" / "a")
    (p / "shot.png").chmod(0)
    try:
        rep = run(tmp_path / "s", tmp_path / "in")
    finally:
        (p / "shot.png").chmod(0o644)
    assert [i.code for i in rep.errors] == ["file_unreadable"]
    assert rep.entries[0].source_ref == "dir:a"


def test_url_list_and_url_observation(tmp_path):
    f = tmp_path / "list.txt"
    f.write_text("HEADER\n\nhttps://A.example/x?gclid=abc&b=1\nhttps://B.example/\nhttps://a.example:443/x?gclid=abc&b=1#f\n")
    rep = run(tmp_path / "s", f, "https://c.example/p")
    assert len(rep.entries) == 3 and rep.ignored_lines == 1
    o = Store(tmp_path / "s").observation(rep.entries[0].observation_id)
    a = o.artifacts[0]
    assert o.input_kind.value == "url_only" and a.local_ref is None and "gclid=***" in a.display_url
    assert run(tmp_path / "s", f).count("unchanged") == 2


def test_detection_without_extension():
    assert sniff(b"(function(){var a=window.x;})()", "hls.js@latest").kind == "js"
    assert sniff(b".a{color:red}\n.b{margin:0}", "x.css.pobrane").kind == "css"
    assert sniff(b"<svg xmlns='http://www.w3.org/2000/svg'/>", "icon").kind == "svg"
    assert sniff(b'{"log":{"version":"1.2","entries":[]}}', "x.json").kind == "har"
    assert sniff(PNG, "nazwa.jpg").kind == "png"
    assert sniff(bytes(range(200)), "x").kind == "unknown"


def test_derived_artifacts_roundtrip(tmp_path):
    make_page(tmp_path / "in" / "a")
    rep = run(tmp_path / "s", tmp_path / "in")
    store = Store(tmp_path / "s")
    obs = store.observation(rep.entries[0].observation_id)
    shot = next(a for a in obs.artifacts if a.role.value == "landing_screenshot")
    before = (tmp_path / "s" / "manifest.json").read_bytes()
    d = store.put_derived(shot.artifact_id, "ocr-v1", "text.json", {"text": "abc"})
    assert store.put_derived(shot.artifact_id, "ocr-v1", "text.json", {"text": "abc"}).artifact_id == d.artifact_id
    assert json.loads(store.read_derived(shot.artifact_id, "ocr-v1", "text.json")) == {"text": "abc"}
    fresh = Store(tmp_path / "s")  # nowa instancja = brak ponownego importu
    assert fresh.artifact(d.artifact_id).parent.artifact_id == shot.artifact_id
    assert d.artifact_id in [c.artifact_id for c in fresh.children(shot.artifact_id)]
    assert fresh.open_bytes(d.artifact_id) if False else fresh.open_bytes(__import__("methadata.contracts.models", fromlist=["ArtifactRef"]).ArtifactRef(artifact_id=d.artifact_id))
    assert run(tmp_path / "s", tmp_path / "in").count("unchanged") == 1
    assert (tmp_path / "s" / "manifest.json").read_bytes() == before


# ----------------------------------------------------------- dane rzeczywiste (pomijane bez BACKUP)
real = pytest.mark.skipif(not DATA.is_dir(), reason="brak katalogu BACKUP")


@real
def test_real_backup(tmp_path):
    srcs = [DATA] + ([URLS] if URLS.is_file() else [])
    rep = run(tmp_path / "s", *srcs)
    man1 = (tmp_path / "s" / "manifest.json").read_bytes()
    c = json.loads(man1)["counts"]
    assert not rep.errors
    assert c["page_capture"] == 15 and c["page_capture_unique_canonical"] == 14
    page_alias = [a for a in json.loads(man1)["aliases"] if a["kind"] == "page_capture"]
    assert len(page_alias) == 1 and page_alias[0]["source_refs"] == ["dir:p1", "dir:p2"]
    arch = [e for e in rep.entries if "archive_capture" in e.tags]
    assert len(arch) == 2 and all("http_502" in e.tags and "excluded_from_corpus" in e.tags for e in arch)
    assert arch[0].canonical_id == arch[1].canonical_id  # ZIP == rozpakowana kopia, oba źródła zachowane
    if URLS.is_file():
        assert c["url_observation"] == 3
    # inwentarz plików == manifest (pliki + wpisy ZIP)
    files = [p for p in DATA.rglob("*") if p.is_file() and p.name != ".DS_Store"]
    inv = [json.loads(l) for l in (tmp_path / "s" / "inventory.jsonl").read_text().splitlines()]
    plain = [r for r in inv if r["role"] != "input_url" and "!/" not in (r["origin"] or "")]
    assert len(plain) == len(files)
    assert sum("!/" in (r["origin"] or "") for r in inv) == 15
    assert not [i for i in rep.issues if i.severity != "info"]
    # powtórny import: stabilne liczby i manifest
    rep2 = run(tmp_path / "s", *srcs)
    assert rep2.count("unchanged") == len(rep.entries) and rep2.blobs_new == 0
    assert (tmp_path / "s" / "manifest.json").read_bytes() == man1
    assert verify_blobs(Store(tmp_path / "s")) == []
    # typy .js.pobrane, hls.js@latest
    ms = {r["media_type"] for r in inv if r["origin"] and (r["origin"].endswith(".js.pobrane") or r["origin"].endswith("hls.js@latest"))}
    assert ms == {"text/javascript"}


@real
def test_real_renamed_copy(tmp_path):
    shutil.copytree(DATA / "p15", tmp_path / "inne_imie")
    rep = run(tmp_path / "s", tmp_path / "inne_imie")
    assert rep.count("new") == 1 and not rep.errors
