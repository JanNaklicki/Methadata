import json
import os
import shutil
from pathlib import Path

import pytest

from methadata.baseline import BaselineError, load_baseline_config, run_baseline
from methadata.baseline.__main__ import main as baseline_main
from methadata.ingest.__main__ import main as ingest_main

ROOT = Path(__file__).resolve().parents[3]
CONFIG = ROOT / "configs" / "baseline" / "baseline.json"
BACKUP = Path(os.environ.get("METHADATA_BACKUP", ROOT.parents[2] / "BACKUP"))
if not BACKUP.is_dir():  # worktree w .claude/worktrees/<id>: BACKUP leży w głównym katalogu projektu
    BACKUP = next((p / "BACKUP" for p in ROOT.parents if (p / "BACKUP").is_dir()), BACKUP)
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24


def img(n: int) -> bytes:
    return PNG + bytes([n]) * 8


def page(d: Path, images, html_extra=""):
    d.mkdir(parents=True)
    (d / "page.html").write_text(f"<!doctype html><html><head><title>t{html_extra}</title></head><body>{html_extra}</body></html>")
    (d / "shot.png").write_bytes(img(200 + len(html_extra)))
    (d / "page_files").mkdir()
    for n in images:
        (d / "page_files" / f"i{n}.png").write_bytes(img(n))


def synthetic(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    page(src / "p1", [1, 2, 3, 4], "a")
    shutil.copytree(src / "p1", src / "p2")                  # kopia bajt w bajt -> alias
    page(src / "p3", [1, 2, 3, 4], "b")                      # ta sama rodzina, inny HTML
    page(src / "p4", [1, 2, 3, 4, 5, 6], "c")                # 4/6 = 0,667
    page(src / "p5", [10, 11], "d")
    page(src / "p6", [10, 12, 13, 14, 15], "e")              # 1/6 = 0,167 z p5
    page(src / "p7", [], "f")                                # brak grafik
    (src / "urls.txt").write_text("https://example.invalid/a?utm=1\n")
    store = tmp_path / "store"
    assert ingest_main(["import", "--store", str(store), str(src / "p1"), str(src / "p2"), str(src / "p3"), str(src / "p4"),
                        str(src / "p5"), str(src / "p6"), str(src / "p7"), str(src / "urls.txt")]) == 0
    return store


def cfg(**over):
    c = load_baseline_config(CONFIG)
    c["corpus"]["exclude_source_refs"] = ["dir:p1"]
    c["expected"] = {"materials": 6, "pairs": 15, "pairs_with_shared_images": 4, "threshold": 0.5,
                     "groups": [["p2", "p3", "p4"]], "scores": {"p2-p3": 1.0, "p3-p4": 0.6667}}
    c.pop("frozen_run_config_hash", None)
    c.update(over)
    return c


def test_synthetic_baseline(tmp_path):
    store = synthetic(tmp_path)
    res = run_baseline(store, cfg())
    rep = res["report"]
    assert all(c["ok"] for c in rep["checks"]), rep["checks"]
    assert rep["groups"] == [["p2", "p3", "p4"]] and rep["singletons"] == ["p5", "p6", "p7"]
    assert len(rep["standalone_urls"]) == 1
    p = next(x for x in rep["pairs"] if {x["a"], x["b"]} == {"p3", "p4"})
    assert p["shared_images"] == 4 and len(p["shared_sha256"]) == 4 and "4/6" in p["explanation"]
    assert next(x for x in rep["pairs"] if {x["a"], x["b"]} == {"p5", "p7"})["score"] is None
    # powtórzenie daje ten sam przebieg
    assert run_baseline(store, cfg())["record"]["run_id"] == res["record"]["run_id"]


def test_synthetic_cli_and_outputs(tmp_path, capsys):
    store = synthetic(tmp_path)
    c = cfg()
    conf = tmp_path / "c.json"
    conf.write_text(json.dumps(c))
    assert baseline_main(["run", "--store", str(store), "--config", str(conf), "--out", str(tmp_path / "out")]) == 0
    assert "{p2,p3,p4}" in capsys.readouterr().out
    for f in ("run.json", "bundle.json", "baseline_report.json"):
        assert (tmp_path / "out" / f).is_file()


def test_input_mismatch_fails(tmp_path):
    store = synthetic(tmp_path)
    c = cfg()
    c["expected"]["materials"] = 14
    bad = [x for x in run_baseline(store, c)["report"]["checks"] if not x["ok"]]
    assert bad and "14" in bad[0]["name"]
    conf = tmp_path / "c.json"
    conf.write_text(json.dumps(c))
    assert baseline_main(["run", "--store", str(store), "--config", str(conf), "--out", str(tmp_path / "o")]) == 1


def test_frozen_config_and_empty_corpus(tmp_path):
    assert load_baseline_config(CONFIG)["run"]["cluster_config"]["threshold"] == 0.5  # hash zgodny
    c = json.loads(CONFIG.read_text())
    c["run"]["cluster_config"]["threshold"] = 0.4
    p = tmp_path / "c.json"
    p.write_text(json.dumps(c))
    with pytest.raises(BaselineError, match="zamrożon"):
        load_baseline_config(p)
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(BaselineError, match="pusty korpus"):
        run_baseline(empty, cfg())


@pytest.mark.skipif(not BACKUP.is_dir(), reason="brak BACKUP z danymi rzeczywistymi")
def test_real_corpus(tmp_path):
    store = tmp_path / "store"
    srcs = [str(BACKUP)] + ([str(BACKUP.parent / "scam_links.dec")] if (BACKUP.parent / "scam_links.dec").is_file() else [])
    assert ingest_main(["import", "--store", str(store), *srcs]) == 0
    rep = run_baseline(store, load_baseline_config(CONFIG))["report"]
    assert all(c["ok"] for c in rep["checks"]), [c for c in rep["checks"] if not c["ok"]]
    assert rep["counts"]["materials"] == 14 and rep["counts"]["pairs"] == 91
    assert rep["groups"] == [["p2", "p7", "p15"], ["p8", "p11"]]
    assert rep["failure_case"]["groups"][0] == ["p2", "p3", "p4", "p7", "p15"]
