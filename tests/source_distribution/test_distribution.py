from pathlib import Path

import pytest

from methadata.contracts import ids
from methadata.contracts.interfaces import AdapterKind
from methadata.contracts.models import Artifact, ArtifactRole, ExtractionState, InputKind, Observation
from methadata.contracts.registry import discover
from methadata.sources.distribution import (DistributionComparator, DistributionExtractor, parse_ads_txt,
                                            parse_schain, parse_sellers_json, seller_graph_edges)
from methadata.sources.distribution.adapter import AUTHORIZED, OBSERVED

S = Path(__file__).parent / "samples"
T = "2026-11-01T10:00:00Z"


class Mem:
    def __init__(self):
        self.d = {}

    def open_bytes(self, ref):
        return self.d[ref.artifact_id]


def D(sample, *, origin=None, url=None):
    """Dokument (role=document): sample to nazwa pliku z samples/ albo surowe bajty."""
    data = sample if isinstance(sample, bytes) else (S / sample).read_bytes()
    return (data, origin or sample, url)


def obs(name, mem, *docs):
    oid = ids.observation_id("synthetic://" + name, "full_capture")
    arts = []
    for i, (data, origin, url) in enumerate(docs):
        sha = ids.sha256_bytes(data)
        a = Artifact(artifact_id=ids.artifact_id(oid, ArtifactRole.document.value, sha, f"{name}-{i}"), observation_id=oid,
                     role=ArtifactRole.document, sha256=sha, local_ref=f"x/{i}", media_type="application/octet-stream",
                     origin=origin, url=url)
        mem.d[a.artifact_id] = data
        arts.append(a)
    return Observation(observation_id=oid, canonical_id=ids.canonical_id([], url="synthetic://" + name),
                       source_ref="synthetic://" + name, tags=["synthetic"], capture_time=T,
                       input_kind=InputKind.full_capture, artifacts=arts)


PUB_A = D("ads_pub_a.txt", origin="ads.txt", url="https://pub-a.example.test/ads.txt")
PUB_B = D("ads_pub_b.txt", origin="ads.txt", url="https://pub-b.example.test/ads.txt")
SELLERS = D("sellers_adnet.json", origin="sellers.json", url="https://adnet.example.test/sellers.json")
SCH_A = D("schain_a.json", origin="schain_a.json")
SCH_B = D("schain_b.json", origin="schain_b.json")
SCH_C = D("schain_c.json", origin="schain_c.json")


def extract(o, mem):
    return DistributionExtractor().extract(o, reader=mem)


def run(*observations, mem):
    feats = {}
    for o in observations:
        f, s = extract(o, mem)
        assert all(x.state in (ExtractionState.ok, ExtractionState.missing_input) for x in s), s
        feats[o.observation_id] = f
    return feats


def vals(feats, oid, kind):
    return [f.value for f in feats[oid] if f.kind == kind]


def cmp(a, b, feats):
    [pe] = DistributionComparator().compare(a, b, feats, run_id="r1")
    return pe


# ---------------------------------------------------------------- rejestracja

def test_registered():
    found = discover()
    assert {"source-distribution-extractor", "source-distribution-compare"} <= set(found)
    assert discover(AdapterKind.comparator)["source-distribution-compare"].spec.kind is AdapterKind.comparator


# ---------------------------------------------------------------- parsery

def test_parse_ads_txt_entries_variables_and_issues():
    p = parse_ads_txt((S / "ads_pub_a.txt").read_text(encoding="utf-8"))
    recs = {(r["ad_system"], r["account_id"], r["relationship"]): r for r in p["records"]}
    assert ("adnet.example.test", "1111", "DIRECT") in recs
    assert ("adnet.example.test", "2222", "RESELLER") in recs
    assert ("ssp.example.test", "3333", "RESELLER") in recs  # komentarz w linii nie psuje wpisu
    assert recs[("adnet.example.test", "1111", "DIRECT")]["cert_authority_id"] == "abc123"
    assert recs[("ssp.example.test", "3333", "RESELLER")]["cert_authority_id"] is None
    assert p["variables"] == {"CONTACT": ["ads@pub-a.example.test"], "OWNERDOMAIN": ["pub-a.example.test"]}
    assert len(p["issues"]) == 1 and "wiersz" in p["issues"][0]  # wiersz bez przecinków


def test_parse_ads_txt_ambiguous_rows():
    p = parse_ads_txt(",9999,DIRECT\n123,RESELLER\nAdnet.Example.Test, 1, direct\n")
    amb = [r for r in p["records"] if r["ambiguous"]]
    assert {r["account_id"] for r in amb} == {"9999", "123"}
    assert all(r["ad_system"] is None for r in amb)
    named = [r for r in p["records"] if not r["ambiguous"]]
    assert named == [{"ad_system": "adnet.example.test", "account_id": "1", "relationship": "DIRECT",
                      "cert_authority_id": None, "ambiguous": False}]


def test_parse_ads_txt_bad_domain_is_issue_not_record():
    p = parse_ads_txt("Zla Nazwa!, 1, DIRECT\n")
    assert p["records"] == [] and "niepoprawna nazwa" in p["issues"][0]


def test_parse_sellers_json_confidential():
    import json
    p = parse_sellers_json(json.loads((S / "sellers_adnet.json").read_text(encoding="utf-8")))
    pub = p["sellers"]["1111"]
    assert pub["name"] == "Pub Alpha Inc" and pub["domain"] == "pub-a.example.test"
    assert pub["seller_type"] == "PUBLISHER" and pub["is_confidential"] is False
    conf = p["sellers"]["7777"]
    assert conf["is_confidential"] is True and conf["name"] is None and conf["domain"] is None
    assert p["contact_email"] == "sellers@adnet.example.test"


def test_parse_sellers_json_invalid_structure():
    with pytest.raises(ValueError):
        parse_sellers_json({"nie": "sellers"})


def test_parse_schain_json():
    import json
    p = parse_schain(json.loads((S / "schain_a.json").read_text(encoding="utf-8")))
    assert p["complete"] is True and p["ver"] == "1.0" and len(p["nodes"]) == 2
    assert [n["hop"] for n in p["nodes"]] == [0, 1]
    last = p["nodes"][1]
    assert (last["ad_system"], last["seller_id"], last["hp"]) == ("adnet.example.test", "1111", True)
    assert parse_schain(json.loads((S / "schain_c.json").read_text(encoding="utf-8")))["complete"] is False
    wrapped = parse_schain({"ext": {"schain": json.loads((S / "schain_b.json").read_text(encoding="utf-8"))}})
    assert len(wrapped["nodes"]) == 2


def test_parse_schain_text_format():
    txt = ("1.0,1!ssp.example.test,3333,1,req-1,,!"
           "adnet.example.test,1111,1,req-2,Pub Alpha Inc,pub-a.example.test")
    p = parse_schain(txt)
    assert p["complete"] is True and p["ver"] == "1.0" and len(p["nodes"]) == 2
    assert p["nodes"][1]["seller_id"] == "1111" and p["nodes"][1]["hp"] is True
    assert p["nodes"][1]["name"] == "Pub Alpha Inc" and p["nodes"][0]["hp"] is True
    with pytest.raises(ValueError):
        parse_schain({"bez": "wezlow"})


# ---------------------------------------------------------------- ekstrakcja

def test_authorized_and_observed_features_with_directory():
    mem = Mem()
    o = obs("a", mem, PUB_A, SELLERS, SCH_A)
    f, s = extract(o, mem)
    assert all(x.state is ExtractionState.ok for x in s)
    assert all(x.evidence_refs for x in f)

    auth = {(v["ad_system"], v["account_id"]): v for v in vals({o.observation_id: f}, o.observation_id, "authorized_seller")}
    a1111 = auth[("adnet.example.test", "1111")]
    assert a1111["relation_type"] == AUTHORIZED
    assert a1111["publisher_domain"] == "pub-a.example.test"
    assert a1111["source"] == "https://pub-a.example.test/ads.txt"
    assert a1111["directory"]["seller_type"] == "PUBLISHER" and a1111["directory"]["name"] == "Pub Alpha Inc"
    assert a1111["retrieved_at"] == T
    assert auth[("adnet.example.test", "2222")]["directory"]["name"] == "Reseller Two"
    assert auth[("ssp.example.test", "3333")]["directory"] is None  # brak sellers.json tego systemu
    amb = [v for v in auth.values() if v["ambiguous"]]
    assert len(amb) == 1 and amb[0]["ad_system"] is None and amb[0]["note"]

    obsv = {(v["ad_system"], v["seller_id"]): v for v in vals({o.observation_id: f}, o.observation_id, "observed_seller")}
    seen = obsv[("adnet.example.test", "1111")]
    assert seen["relation_type"] == OBSERVED and seen["authorized_by_publisher"] is True
    assert seen["complete"] is True and seen["directory"]["seller_type"] == "PUBLISHER"
    assert seen["source"] == "schain_a.json" and seen["retrieved_at"] == T

    chain = vals({o.observation_id: f}, o.observation_id, "sales_chain")
    assert len(chain) == 1 and chain[0]["length"] == 2 and chain[0]["relation_type"] == OBSERVED

    variables = {v["name"]: v["values"] for v in vals({o.observation_id: f}, o.observation_id, "ads_variable")}
    assert variables["CONTACT"] == ["ads@pub-a.example.test"]


def test_sellers_file_adds_no_features_of_its_own():
    mem = Mem()
    o = obs("s", mem, SELLERS)
    f, s = extract(o, mem)
    assert f == [] and s[0].state is ExtractionState.ok


def test_authorized_by_publisher_true_false_none():
    mem = Mem()
    a = obs("a", mem, PUB_A, SCH_A)  # schain obok ads.txt
    x = obs("x", mem, PUB_B, SCH_C)  # exchange-x nie jest w ads.txt tego wydawcy
    n = obs("n", mem, SCH_A)  # brak ads.txt -> nie wiadomo
    feats = run(a, x, n, mem=mem)
    byhop = lambda oid: {v["seller_id"]: v["authorized_by_publisher"] for v in vals(feats, oid, "observed_seller")}
    assert byhop(a.observation_id) == {"3333": True, "1111": True}
    assert byhop(x.observation_id) == {"88": False, "5555": True}
    assert set(byhop(n.observation_id).values()) == {None}


def test_edge_types_separate_authorization_and_observation():
    mem = Mem()
    o = obs("e", mem, PUB_A, SCH_A)
    feats = run(o, mem=mem)[o.observation_id]
    edges = seller_graph_edges(feats)
    types = {(e["edge_type"], e["target"]) for e in edges}
    assert (AUTHORIZED, "seller:adnet.example.test:1111") in types
    assert (OBSERVED, "seller:adnet.example.test:1111") in types
    assert {e["edge_type"] for e in edges} == {AUTHORIZED, OBSERVED}
    assert all(e["source"] == o.observation_id for e in edges)
    assert any(e["ambiguous"] for e in edges)  # wpis ,9999,DIRECT


def test_missing_input_for_empty_observation_and_unrelated_document():
    mem = Mem()
    empty = obs("empty", mem, D(b"", origin="empty.txt"))  # Observation wymaga >=1 artefaktu
    f, s = extract(empty, mem)
    assert f == [] and [x.state for x in s] == [ExtractionState.missing_input]
    other = obs("other", mem, D(b"zwykly tekst", origin="readme.txt"))
    f, s = extract(other, mem)
    assert f == [] and s[0].state is ExtractionState.missing_input


def test_invalid_sellers_json_is_failed():
    mem = Mem()
    o = obs("bad", mem, D(b"{to nie jest json", origin="sellers.json", url="https://adnet.example.test/sellers.json"))
    f, s = extract(o, mem)
    assert f == [] and s[0].state is ExtractionState.failed


def test_invalid_schain_json_is_failed():
    mem = Mem()
    o = obs("badchain", mem, D(b'{"ver": "1.0", "nodes": "nie-lista"}', origin="schain_x.json"))
    f, s = extract(o, mem)
    assert f == [] and s[0].state is ExtractionState.failed


# ---------------------------------------------------------------- komparator

def test_same_observed_seller_scores_high():
    mem = Mem()
    a = obs("a", mem, PUB_A, SCH_A)
    b = obs("b", mem, PUB_B, SCH_B)
    feats = run(a, b, mem=mem)
    pe = cmp(a, b, feats)
    assert pe.score == 0.85 and OBSERVED in pe.explanation and "adnet.example.test/1111" in pe.explanation
    assert pe.relation.value == "campaign_candidate" and pe.method_id == "source-distribution-compare"
    assert all(pe.evidence_refs.values())


def test_observed_vs_authorized_scores_half():
    mem = Mem()
    a = obs("a", mem, PUB_A, SCH_A)  # obserwowany adnet/1111
    b = obs("b", mem, PUB_B)  # tylko upoważniony adnet/1111 przez innego wydawcę
    pe = cmp(a, b, run(a, b, mem=mem))
    assert pe.score == 0.5 and OBSERVED in pe.explanation and AUTHORIZED in pe.explanation


def test_shared_authorization_only_scores_low():
    mem = Mem()
    a = obs("a", mem, PUB_A)
    b = obs("b", mem, PUB_B)
    pe = cmp(a, b, run(a, b, mem=mem))
    assert pe.score == 0.35 and AUTHORIZED in pe.explanation and "zdolność" in pe.explanation


def test_same_account_id_without_system_is_ambiguous():
    mem = Mem()
    a = obs("a", mem, PUB_A)  # ,9999,DIRECT -> bez nazwy systemu
    b = obs("b", mem, D(b",9999,DIRECT\n", origin="ads.txt", url="https://pub-c.example.test/ads.txt"))
    pe = cmp(a, b, run(a, b, mem=mem))
    assert pe.score == 0.1 and "niejednoznaczne" in pe.explanation and "9999" in pe.explanation


def test_no_data_gives_none_score():
    mem = Mem()
    a = obs("a", mem, PUB_A)
    n = obs("n", mem, D(b"zwykly tekst", origin="readme.txt"))
    pe = cmp(a, n, run(a, n, mem=mem))
    assert pe.score is None and "brak podstaw" in pe.explanation


def test_different_sellers_score_zero():
    mem = Mem()
    a = obs("a", mem, PUB_A, SCH_A)
    c = obs("c", mem, SCH_C)  # exchange-x/88 i other-ssp/5555: brak wspólnego sprzedawcy
    pe = cmp(a, c, run(a, c, mem=mem))
    assert pe.score == 0.0
