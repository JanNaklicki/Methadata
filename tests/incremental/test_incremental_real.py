import json
from pathlib import Path

import pytest

from methadata.clustering.incremental.__main__ import main as incremental_main
from methadata.clustering.incremental.scenario import load_config, run_scenario
from methadata.devtools.realdata import BACKUP, SCAM_LINKS
from methadata.fusion_eval import load_eval_config
from methadata.ingest.__main__ import main as ingest_main

ROOT = Path(__file__).resolve().parents[2]
BASE_PATH = ROOT / "configs" / "baseline" / "baseline.json"
EVAL = ROOT / "configs" / "fusion" / "eval.json"
INC = ROOT / "configs" / "incremental" / "incremental.json"
BASE = json.loads(BASE_PATH.read_text(encoding="utf-8"))

pytestmark = pytest.mark.skipif(not BACKUP.is_dir(), reason="brak katalogu BACKUP")


@pytest.fixture(scope="module")
def real_store(tmp_path_factory):
    store = tmp_path_factory.mktemp("incremental") / "store"
    srcs = [str(BACKUP)] + ([str(SCAM_LINKS)] if SCAM_LINKS.is_file() else [])
    assert ingest_main(["import", "--store", str(store), *srcs]) == 0
    return store


def test_real_incremental_scenario(real_store):
    rep = run_scenario(real_store, BASE, load_eval_config(EVAL), load_config(INC))
    h = rep["holdout"][0]
    assert h["held_out"] == "p15"
    assert h["decision"] == "joined_cluster"
    assert h["assigned_to"] == ["p2", "p7", "p15"]
    assert h["new_group_identical"] is True
    assert h["cost"]["incremental_pair_comparisons"] < h["cost"]["full_all_pairs"]
    assert rep["leave_one_out_summary"]["diverging"] == []
    for exp in rep["order_experiments"]:
        assert exp["order_independent"] is True, exp["holdout"]


def test_real_incremental_cli(real_store, tmp_path):
    out = tmp_path / "out"
    rc = incremental_main([
        "run",
        "--store", str(real_store),
        "--baseline-config", str(ROOT / "configs" / "baseline" / "baseline.json"),
        "--eval-config", str(ROOT / "configs" / "fusion" / "eval.json"),
        "--config", str(ROOT / "configs" / "incremental" / "incremental.json"),
        "--out", str(out),
    ])
    assert rc == 0
    assert (out / "incremental.json").is_file()
