import json
import sys
from pathlib import Path

import jsonschema
import pytest
from pydantic import ValidationError

from methadata.contracts import (AdapterKind, Bundle, ClusterRun, PairEvidence, discover, ids, mask_url)
from methadata.contracts import schema as schema_mod

FIX = Path(__file__).resolve().parent.parent / "fixtures" / "contract"
VALID = ["complete", "url_only", "duplicate", "missing_resource", "ui_demo"]
INVALID = sorted(p.stem for p in (FIX / "invalid").glob("*.json"))
SCHEMAS = schema_mod.build()


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", VALID)
def test_valid_fixture_passes_pydantic_and_json_schema(name):
    doc = load(FIX / f"{name}.json")
    Bundle.model_validate(doc)
    jsonschema.validate(doc, SCHEMAS["bundle"])


@pytest.mark.parametrize("name", INVALID)
def test_invalid_fixture_rejected(name):
    with pytest.raises(ValidationError):
        Bundle.model_validate(load(FIX / "invalid" / f"{name}.json"))


@pytest.mark.parametrize("name", ["score_above_one", "score_negative"])
def test_schema_level_rejection_of_ranges(name):
    # score poza [0,1] odrzuca też sam JSON Schema (klienci nie-Pythonowi)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(load(FIX / "invalid" / f"{name}.json"), SCHEMAS["bundle"])


def test_score_boundaries():
    base = load(FIX / "ui_demo.json")["pairs"][0]
    for s in (0, 0.0, 1, 1.0, None):
        PairEvidence.model_validate({**base, "score": s} if s is not None else
                                    {**base, "score": None, "evidence_refs": {}})
    for s in (-0.0001, 1.0001, float("nan"), float("inf")):
        with pytest.raises(ValidationError):
            PairEvidence.model_validate({**base, "score": s})


def test_unordered_or_identical_pair_rejected():
    base = load(FIX / "ui_demo.json")["pairs"][0]
    a, b = base["pair"]
    with pytest.raises(ValidationError):
        PairEvidence.model_validate({**base, "pair": [b, a]})
    with pytest.raises(ValidationError):
        PairEvidence.model_validate({**base, "pair": [a, a]})


def test_feature_needs_exactly_value_or_vector():
    b = load(FIX / "ui_demo.json")
    f = b["features"][0]
    for bad in ({**f, "value": 3}, {**f, "value_type": "vector"}, {**f, "value": None}):
        with pytest.raises(ValidationError):
            Bundle.model_validate({**b, "features": [bad]})


def test_cluster_run_status_rules():
    run = load(FIX / "ui_demo.json")["runs"][0]
    run["assignments"][3]["cluster_ids"] = ["c9"]  # singleton z klastrem
    with pytest.raises(ValidationError):
        ClusterRun.model_validate(run)


def test_duplicate_shares_canonical_but_not_observation_id():
    obs = load(FIX / "duplicate.json")["observations"]
    assert obs[0]["canonical_id"] == obs[1]["canonical_id"]
    assert obs[0]["observation_id"] != obs[1]["observation_id"]


def test_url_only_masks_click_ids():
    art = load(FIX / "url_only.json")["observations"][0]["artifacts"][0]
    assert "SECRET123" not in art["display_url"] and "gad_source=5" in art["display_url"]
    assert "SECRET123" in art["url"]


def test_missing_input_distinguishable_from_empty_ok():
    sts = load(FIX / "missing_resource.json")["statuses"]
    assert {s["state"] for s in sts} == {"missing_input", "ok"}
    p = load(FIX / "missing_resource.json")["pairs"][0]
    assert p["score"] is None


def test_stable_ids():
    assert ids.observation_id("s", "full_capture") == ids.observation_id("s", "full_capture")
    assert ids.observation_id("s", "full_capture") != ids.observation_id("t", "full_capture")
    h = "a" * 64
    c1 = ids.canonical_id([("landing_html", h), ("har", h)])
    assert c1 == ids.canonical_id([("har", h), ("landing_html", h), ("resource", "b" * 64)])  # kolejność, zasoby poza identyfikacją
    assert ids.canonical_id([], url="https://x/") != ids.canonical_id([], url="https://y/")
    assert ids.pair_evidence_id("r", ("b", "a"), "m", "shared_template") == ids.pair_evidence_id("r", ("a", "b"), "m", "shared_template")
    # fixtures: ID wyliczone z treści zgadzają się z zapisanymi
    for o in load(FIX / "ui_demo.json")["observations"]:
        assert o["observation_id"] == ids.observation_id(o["source_ref"], o["input_kind"])
        assert o["canonical_id"] == ids.canonical_id([(a["role"], a["sha256"]) for a in o["artifacts"]])


def test_mask_url():
    assert mask_url("https://h/p?gclid=abc&x=1") == "https://h/p?gclid=***&x=1"
    assert mask_url("https://h/p") == "https://h/p"


def test_committed_schemas_in_sync():
    for name, s in SCHEMAS.items():
        committed = json.loads((schema_mod.DEFAULT_DIR / f"{name}.json").read_text(encoding="utf-8"))
        assert committed == s, f"{name}.json nieaktualny: uruchom python -m methadata.contracts.schema"


def test_two_adapters_discovered_without_central_list():
    for d in ("ext_a", "ext_b"):
        sys.path.insert(0, str(FIX / d))
    try:
        found = discover()
        assert {"demo-extractor-v1", "demo-comparator-v1"} <= set(found)
        assert set(discover(AdapterKind.comparator)) >= {"demo-comparator-v1"}
        assert "demo-extractor-v1" not in discover(AdapterKind.comparator)
        assert found["demo-comparator-v1"].spec.optional_inputs[0].kind == "vector"
    finally:
        for d in ("ext_a", "ext_b"):
            sys.path.remove(str(FIX / d))


def test_discovery_without_extensions_does_not_raise():
    assert isinstance(discover(), dict)
