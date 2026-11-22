import json
from io import BytesIO
from zipfile import ZipFile

from fastapi.testclient import TestClient

from methadata_api.app import create_app
from methadata_api.settings import Settings


def archive(files):
    result = BytesIO()
    with ZipFile(result, "w") as out:
        for name, content in files.items():
            out.writestr(name, content)
    return result.getvalue()


def test_backup_import_deduplicates_a_capture_across_packages_and_survives_restart(tmp_path):
    settings = Settings(tmp_path)
    html = "<html><body><h1>Przykładowa reklama</h1></body></html>"
    backup = archive({
        "BACKUP/p1/page.html": html,
        "BACKUP/p2/page.html": html.replace("Przykładowa", "Inna"),
        "__MACOSX/._BACKUP": "metadata",
    })
    with TestClient(create_app(settings)) as api:
        assert api.get("/api/v1/collection").json()["graph"] is None
        response = api.post("/api/v1/imports", files={"files": ("backup.zip", backup)})
        assert response.status_code == 201
        report = response.json()
        assert [(row["label"], row["status"]) for row in report["entries"]] == [("p1", "added"), ("p2", "added")]
        assert api.get(response.headers["location"]).json() == report

    with TestClient(create_app(settings)) as api:
        single = archive({"page.html": html})
        response = api.post("/api/v1/imports", files={"files": ("page.zip", single)})
        assert response.json()["entries"] == [{
            "label": "page.zip", "status": "duplicate", "material_id": report["entries"][0]["material_id"],
        }]
        current = api.get("/api/v1/collection").json()
        assert current["version"] == 1
        assert len(current["graph"]["materials"]) == 2
        assert len(current["sources"][report["entries"][0]["material_id"]]) == 2

        response = api.post("/api/v1/imports", files={"files": ("empty.zip", archive({}))})
        assert response.json()["entries"][0]["status"] == "skipped"
        assert api.get("/api/v1/collection").json()["version"] == 1

        response = api.post("/api/v1/imports", files={"files": ("new.html", html.replace("reklama", "strona"))})
        assert response.json()["entries"][0]["status"] == "added"
        assert api.get("/api/v1/collection").json()["version"] == 2


def test_concurrent_copies_publish_one_material(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    html = b"<html><body><h1>Material do sprawdzenia</h1></body></html>"
    with TestClient(create_app(Settings(tmp_path))) as api:
        def upload(label):
            response = api.post("/api/v1/imports", files={"files": (label, html)})
            assert response.status_code == 201
            return response.json()["entries"][0]

        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = pool.map(upload, ("pierwszy.html", "drugi.html"))

        assert {first["status"], second["status"]} == {"added", "duplicate"}
        assert first["material_id"] == second["material_id"]
        current = api.get("/api/v1/collection").json()
        assert current["version"] == 1
        assert len(current["sources"][first["material_id"]]) == 2


def wait_for_import(api, url):
    import time

    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        report = api.get(url).json()
        if report["status"] in ("succeeded", "failed"):
            return report
        time.sleep(0.02)
    raise AssertionError("import did not finish")


def test_nested_zip_and_orphan_members_are_downloadable_and_manageable(tmp_path):
    html = b"<html><body>Example capture</body></html>"
    nested = archive({"html/source.html": html, "extra.txt": b"inside"})
    outer = archive({"p1/page.html": html, "p1/details.zip": nested, "notes.txt": b"outside",
                     "__MACOSX/._notes.txt": b"metadata", "../unsafe.txt": b"unsafe",
                     "p2/page.html": b"<html><body>Another capture</body></html>"})
    collection = "/api/v1/collection"
    with TestClient(create_app(Settings(tmp_path))) as api:
        response = api.post("/api/v1/imports", files={"files": ("backup.zip", outer)},
                            headers={"Prefer": "respond-async"})
        assert response.status_code == 202
        report = wait_for_import(api, response.headers["location"])
        assert report["status"] == "succeeded"
        assert report["archive"] == {"loaded": 6, "skipped": 1, "metadata": 1}
        assert {entry["label"] for entry in report["entries"]} == {"p1", "p2", "notes.txt"}
        upload, = api.get(collection + "/uploads").json()
        upload_path = f"{collection}/uploads/{upload['id']}"
        response = api.get(upload_path + "/download")
        assert response.content == outer
        assert response.headers["content-disposition"].startswith("attachment")
        member = next(m for m in upload["members"] if m["path"].endswith("details.zip!/extra.txt"))
        assert api.get(f"{upload_path}/members/{member['id']}/download").content == b"inside"
        original = api.get(collection).json()
        assert len(original["graph"]["materials"]) == 2
        material = next(m for m in upload["materials"] if m["label"] == "p1")
        material_path = f"{collection}/materials/{material['id']}"
        assert api.patch(material_path, json={"archived": True, "label": "New name"}).status_code == 200
        assert len(api.get(collection).json()["graph"]["materials"]) == 1
        assert api.patch(upload_path, json={"archived": True}).status_code == 200
        assert api.get(collection).json()["graph"] is None
        assert api.get(f"/api/v1/runs/{original['run_id']}").status_code == 200
        assert api.patch(upload_path, json={"archived": False}).status_code == 200
        assert len(api.get(collection).json()["graph"]["materials"]) == 1

    with TestClient(create_app(Settings(tmp_path))) as api:
        assert api.get(upload_path + "/download").content == outer
        assert api.patch(material_path, json={"archived": False}).status_code == 200
        labels = {m["label"] for m in api.get(collection).json()["graph"]["materials"]}
        assert labels == {"New name", "p2"}


def test_failed_analysis_retries_without_reupload_and_observation_file_survives_restart(tmp_path, monkeypatch):
    from methadata_api.collection import Collection

    html = b"<html><body>Original file bytes</body></html>"
    original_publish = Collection._publish

    def fail(*args):
        raise RuntimeError("test analysis failure")

    monkeypatch.setattr(Collection, "_publish", fail)
    uploads_url = "/api/v1/collection/uploads"
    with TestClient(create_app(Settings(tmp_path))) as api:
        response = api.post("/api/v1/observations", files={"file": ("capture.html", html)})
        assert response.status_code == 201
        upload, = api.get(uploads_url).json()
        import_path = f"/api/v1/imports/{upload['import_id']}"
        assert wait_for_import(api, import_path)["status"] == "failed"
        assert api.get(f"{uploads_url}/{upload['id']}/download").content == html

    monkeypatch.setattr(Collection, "_publish", original_publish)
    with TestClient(create_app(Settings(tmp_path))) as api:
        assert len(api.get(uploads_url).json()) == 1
        assert api.post(import_path + "/retry").status_code == 202
        assert wait_for_import(api, import_path)["status"] == "succeeded"
        assert len(api.get(uploads_url).json()) == 1


def test_restart_resumes_a_persisted_import(tmp_path, monkeypatch):
    from methadata_api.collection import Collection

    html = b"<html><body>Persisted upload</body></html>"
    schedule = Collection.schedule

    def interrupted(self, import_id):
        report = self.report(import_id)
        report.update(status="running", stage="Analiza materiałów")
        self._save_report(report)

    monkeypatch.setattr(Collection, "schedule", interrupted)
    with TestClient(create_app(Settings(tmp_path))) as api:
        response = api.post("/api/v1/imports", files={"files": ("file.html", html)},
                            headers={"Prefer": "respond-async"})
        assert response.json()["status"] == "queued"
        import_path = response.headers["location"]
        assert api.get(import_path).json()["status"] == "running"

    monkeypatch.setattr(Collection, "schedule", schedule)
    with TestClient(create_app(Settings(tmp_path))) as api:
        assert wait_for_import(api, import_path)["status"] == "succeeded"
        assert len(api.get("/api/v1/collection/uploads").json()) == 1


def test_migrates_legacy_blob_once_without_importing_seed(tmp_path):
    from methadata.ingest import FileImporter
    from methadata_api.storage import Storage

    settings = Settings(tmp_path / "data")
    storage = Storage(settings)
    storage.seed_demo()
    html = b"<html><body>Before the collection existed</body></html>"
    source = tmp_path / "legacy.html"
    source.write_bytes(html)
    entry, = FileImporter().import_sources([source], storage.store()).entries
    storage.set_label(entry.observation_id, "Old upload")
    path = "/api/v1/collection/uploads"
    first_id = None
    for _ in range(2):
        with TestClient(create_app(settings)) as api:
            row, = api.get(path).json()
            assert row["label"] == "Old upload"
            assert api.get(f"{path}/{row['id']}/download").content == html
            assert first_id is None or row["id"] == first_id
            first_id = row["id"]


def test_corrupt_and_over_budget_members_do_not_hide_readable_files(tmp_path, monkeypatch):
    from dataclasses import replace

    from methadata_api import collection_archive

    html = b"<html><body>Preserved</body></html>"
    payload = archive({"p1/page.html": html, "damaged.txt": b"unique-bytes", "too-big.txt": b"a" * 160})
    payload = payload.replace(b"unique-bytes", b"broken-bytes")
    monkeypatch.setattr(collection_archive, "LIMITS", replace(collection_archive.LIMITS, max_total_bytes=120))
    with TestClient(create_app(Settings(tmp_path))) as api:
        response = api.post("/api/v1/imports", files={"files": ("backup.zip", payload)})
        report = response.json()
        assert report["status"] == "succeeded"
        assert report["archive"] == {"loaded": 1, "skipped": 2, "metadata": 0}
        assert {issue["label"].rsplit("/", 1)[-1] for issue in report["issues"]} == {"damaged.txt", "too-big.txt"}
        row, = api.get("/api/v1/collection/uploads").json()
        assert api.get(f"/api/v1/collection/uploads/{row['id']}/download").content == payload


def test_zip_with_only_a_nested_package_detects_each_page(tmp_path):
    nested = archive({"p1/page.html": b"<html><body>First capture</body></html>",
                      "p2/page.html": b"<html><body>Second capture</body></html>"})
    with TestClient(create_app(Settings(tmp_path))) as api:
        payload = archive({"pages.zip": nested})
        report = api.post("/api/v1/imports", files={"files": ("backup.zip", payload)}).json()
        assert report["status"] == "succeeded"
        assert report["archive"]["loaded"] == 3
        graph = api.get("/api/v1/collection").json()["graph"]
        assert {m["label"] for m in graph["materials"]} == {"p1", "p2"}


PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360f8cfc0f01f0005000201a5f645400000000049454e44ae426082"
)


def test_shared_image_artifact_exposes_a_servable_preview(tmp_path):
    page = "<html><body><h1>Reklama</h1><img src='ad.png'></body></html>"
    backup = archive({
        "BACKUP/p1/page.html": page,
        "BACKUP/p1/page_files/ad.png": PNG,
        "BACKUP/p2/page.html": page.replace("Reklama", "Inna reklama"),
        "BACKUP/p2/page_files/ad.png": PNG,
    })
    with TestClient(create_app(Settings(tmp_path))) as api:
        assert api.post("/api/v1/imports", files={"files": ("backup.zip", backup)}).status_code == 201
        artifacts = api.get("/api/v1/collection").json()["graph"]["investigation"]["artifacts"]
        images = [item for item in artifacts if item["kind"] == "image"]
        assert images, artifacts
        preview = images[0]["preview"]
        assert preview["media_type"] == "image/png"
        assert preview["href"].startswith("/static/previews/")
        assert api.get(preview["href"]).status_code == 200
        assert all("preview" not in item for item in artifacts if item["kind"] != "image")


def test_snapshot_saved_before_previews_is_upgraded_once(tmp_path):
    page = "<html><body><img src='ad.png'></body></html>"
    backup = archive({
        "BACKUP/p1/page.html": page, "BACKUP/p1/page_files/ad.png": PNG,
        "BACKUP/p2/page.html": page + "<!-- 2 -->", "BACKUP/p2/page_files/ad.png": PNG,
    })
    with TestClient(create_app(Settings(tmp_path))) as api:
        api.post("/api/v1/imports", files={"files": ("backup.zip", backup)})
        run_id = api.get("/api/v1/collection").json()["run_id"]
        saved = next(tmp_path.rglob(f"{run_id}/result.json"))
        legacy = json.loads(saved.read_text(encoding="utf-8"))
        legacy["investigation"].pop("artifact_previews")
        for item in legacy["investigation"]["artifacts"]:
            item.pop("preview", None)
        saved.write_text(json.dumps(legacy), encoding="utf-8")

        upgraded = api.get("/api/v1/collection").json()["graph"]["investigation"]
        assert any("preview" in item for item in upgraded["artifacts"] if item["kind"] == "image")
        assert json.loads(saved.read_text(encoding="utf-8"))["investigation"]["artifact_previews"] is True


def test_material_text_is_searchable_by_keyword_even_when_not_shared(tmp_path):
    backup = archive({
        "BACKUP/p1/page.html": "<html><head><title>Kardivix na ciśnienie</title></head><body><p>Kardivix obniża ciśnienie w tydzień, twierdzi lekarz.</p></body></html>",
        "BACKUP/p2/page.html": "<html><head><title>Pożyczka online</title></head><body><p>Decyzja w kilka minut bez zaświadczeń.</p></body></html>",
    })
    with TestClient(create_app(Settings(tmp_path))) as api:
        assert api.post("/api/v1/imports", files={"files": ("backup.zip", backup)}).status_code == 201
        index = api.get("/api/v1/collection").json()["graph"]["investigation"]["search_text"]
        hits = [mid for mid, text in index.items() if "kardivix" in text]
        assert len(hits) == 1 and not any("kardivix" in text for mid, text in index.items() if mid not in hits)
