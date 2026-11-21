import copy
import json
from pathlib import Path

import pytest

from methadata.contracts import discover
from methadata.devtools.realdata import BACKUP, SCAM_LINKS
from methadata.fusion_eval import EvalError, load_eval_config, run_eval, write_outputs
from methadata.fusion_eval.__main__ import main as eval_main
from methadata.ingest.__main__ import main as ingest_main

ROOT = Path(__file__).resolve().parents[3]
BASE = json.loads((ROOT / "configs" / "baseline" / "baseline.json").read_text(encoding="utf-8"))
EVAL = ROOT / "configs" / "fusion" / "eval.json"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
LOREM = "Pilne ostrzezenie o oszustwie inwestycyjnym znanej osoby publicznej. " * 6


def img(n):
    return PNG + bytes([n]) * 8


def page(d: Path, images, body, text):
    d.mkdir(parents=True)
    (d / "page.html").write_text(f"<!doctype html><html><head><title>t</title></head><body>{body}<main><h1>{text}</h1>"
                                 f"<p>{text}</p></main></body></html>")
    (d / "shot.png").write_bytes(img(200 + len(text) % 50))
    (d / "page_files").mkdir()
    for n in images:
        (d / "page_files" / f"i{n}.png").write_bytes(img(n))


def synthetic(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    tpl = "<header><nav><a>x</a></nav></header><section><div><span>a</span></div><div><span>b</span></div></section>"
    page(src / "p2", [1, 2, 3, 4], tpl, LOREM + " alfa")
    page(src / "p3", [1, 2, 3, 4], tpl, LOREM + " beta")              # te same grafiki, ten sam szablon i tekst
    page(src / "p4", [], tpl, LOREM + " gamma")                       # brak grafik: szablon + tekst, bez zasobów
    page(src / "p5", [20, 21], "<div><ul><li>q</li><li>r</li></ul></div>", "Zupelnie inna tresc o gotowaniu zup warzywnych w domu.")
    (src / "urls.txt").write_text("https://example.invalid/a?utm=1\n")
    store = tmp_path / "store"
    assert ingest_main(["import", "--store", str(store), *[str(src / f"p{i}") for i in (2, 3, 4, 5)], str(src / "urls.txt")]) == 0
    return store


def syn_cfg():
    c = load_eval_config(EVAL)
    c["reference"] = {"families": [["p2", "p3"]], "campaign_confirmed": [["p2", "p3"]]}
    c["control_cases"] = {"p2/p3": ["p2", "p3"]}
    return c


def syn_base():
    b = copy.deepcopy(BASE)
    b["corpus"]["exclude_source_refs"] = []
    return b


def test_adapters_discovered():
    ad = discover(strict=False)
    cfg = load_eval_config(EVAL)
    for m in [*cfg["extractors"], *(f["method_id"] for f in cfg["families"].values())]:
        assert m in ad, m


def test_config_validation(tmp_path):
    bad = json.loads(EVAL.read_text())
    bad["profiles"]["szablon"]["families"] = ["nieznana"]
    p = tmp_path / "e.json"
    p.write_text(json.dumps(bad))
    with pytest.raises(EvalError, match="nieznane rodziny"):
        load_eval_config(p)


def test_synthetic_eval_and_outputs(tmp_path, capsys):
    store = synthetic(tmp_path)
    res = run_eval(store, syn_base(), syn_cfg())
    rep = res["report"]
    P = rep["profiles"]
    assert P["baseline"]["groups"] == [["p2", "p3"]]
    assert P["kampania"]["groups"] == [["p2", "p3"]] or ["p2", "p3", "p4"] in P["kampania"]["groups"]
    # p4 nie ma grafik: sam szablon + tekst; profil z samymi zasobami go nie łączy
    assert "p4" in P["baseline"]["singletons"]
    # URL-only: porównań nie da się wykonać, żadnego dowodu
    uo = rep["controls"]["url-only"]["kampania"]
    assert set(uo["statuses"]) == {"no_evidence"} and uo["pairs_with_evidence"] == 0 and uo["singletons"] == ["url-1"]
    assert P["kampania"]["metrics"]["coverage_loss"] > P["kampania"]["metrics"]["coverage_loss_pages"] == 0.0
    # ablacja: każda rodzina profilu ma wpis i diff
    for fam in ("asset", "text", "dom"):
        assert f"kampania-bez-{fam}" in P and f"kampania-bez-{fam}" in rep["diffs"]
        assert set(rep["contribution"]["kampania"]) == {"asset", "text", "dom"}
    assert "baseline->kampania" in rep["diffs"]
    # powtórzenie daje ten sam wynik
    assert run_eval(store, syn_base(), syn_cfg())["report"]["profiles"]["kampania"]["run_id"] == P["kampania"]["run_id"]
    out = write_outputs(res, tmp_path / "out")
    assert json.loads((out / "fusion_eval.json").read_text())["dataset_version"] == rep["dataset_version"]
    assert "Rekomendacja dla R02" in (out / "fusion_eval.md").read_text()


def test_synthetic_cli(tmp_path, capsys):
    store = synthetic(tmp_path)
    b = tmp_path / "b.json"
    b.write_text(json.dumps(syn_base()))
    e = tmp_path / "e.json"
    e.write_text(json.dumps(syn_cfg()))
    assert eval_main(["run", "--store", str(store), "--baseline-config", str(b), "--config", str(e), "--out", str(tmp_path / "o")]) == 0
    assert "Ocena fuzji F06" in capsys.readouterr().out
    assert (tmp_path / "o" / "fusion_eval.md").is_file()


def test_reference_must_exist(tmp_path):
    store = synthetic(tmp_path)
    c = syn_cfg()
    c["reference"]["families"] = [["p2", "p99"]]
    with pytest.raises(EvalError, match="nie występuje"):
        run_eval(store, syn_base(), c)


# ------------------------------------------------------------ rzeczywisty korpus (pomijany bez BACKUP)

@pytest.mark.skipif(not BACKUP.is_dir(), reason="brak katalogu BACKUP")
def test_real_corpus(tmp_path):
    store = tmp_path / "store"
    srcs = [str(BACKUP)] + ([str(SCAM_LINKS)] if SCAM_LINKS.is_file() else [])
    assert ingest_main(["import", "--store", str(store), *srcs]) == 0
    rep = run_eval(store, BASE, load_eval_config(EVAL))["report"]
    P = rep["profiles"]
    assert rep["dataset_version"].endswith(BASE["expected"]["dataset_fingerprint"])
    # baza = pomiar F04
    assert P["baseline"]["groups"] == [["p2", "p7", "p15"], ["p8", "p11"]]
    fam = [["p2", "p7", "p15"], ["p8", "p11"]]
    # p2/p7/p15 stabilne w każdym profilu i po wyłączeniu każdej rodziny
    for n, r in P.items():
        assert ["p2", "p7", "p15"] in r["groups"], n
        assert r["metrics"]["vs_template_ref"]["merged_groups"] == [], n          # brak fałszywego mostu
    # p3/p4: szablon i narracja tak, kampania ścisła (wymaga zasobów) nie
    assert ["p3", "p4"] in P["szablon"]["groups"] and ["p3", "p4"] in P["kampania"]["groups"]
    assert ["p3", "p4"] not in P["baseline"]["groups"] and ["p3", "p4"] not in P["kampania-scisla"]["groups"]
    assert P["kampania-scisla"]["groups"] == fam
    c = rep["diffs"]["baseline->kampania"]["edge_changes"]
    assert [x["pair"] for x in c] == [["p3", "p4"]] and set(c[0]["evidence_after"]) == {"asset", "content-text", "dom-structure"}
    # wkład: bez tekstu lub DOM kampania traci p3-p4; bez zasobów nie
    cont = rep["contribution"]["kampania"]
    assert cont["text"]["accepted_edges_lost"] == [["p3", "p4"]] and cont["dom"]["accepted_edges_lost"] == [["p3", "p4"]]
    assert cont["asset"]["accepted_edges_lost"] == []
    # URL-only: 3 adresy, brak jakiegokolwiek dowodu, singletony
    assert len(rep["corpus"]["urls"]) == 3
    uo = rep["controls"]["url-only"]["kampania"]
    assert uo["pairs_with_evidence"] == 0 and uo["singletons"] == ["url-1", "url-2", "url-3"]
    # przypadek zawodności: sam niski próg skleja rodzinę p2/p7/p15 z p3/p4 (F04), bramka wsparcia nie
    fc = rep["failure_cases"]
    assert ["p2", "p3", "p4", "p7", "p15"] in fc["probe_low_threshold_without_support_gate"]["baseline"]["groups"]
    assert fc["probe_low_threshold"]["baseline"]["merged_groups"] == []
