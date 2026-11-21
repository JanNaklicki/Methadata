import json

from methadata.contracts import ids
from methadata.contracts.interfaces import AdapterKind
from methadata.contracts.models import (Artifact, ArtifactRole, Bundle, ExtractionState, InputKind, Observation)
from methadata.contracts.registry import discover
from methadata.sources.payer import PayerComparator, PayerExtractor, group_by_payer, normalize_entity


class Mem:
    def __init__(self):
        self.d = {}

    def open_bytes(self, ref):
        return self.d[ref.artifact_id]


def ad(name, rec, mem, raw=None):
    data = raw if raw is not None else json.dumps(rec, ensure_ascii=False).encode()
    oid = ids.observation_id("synthetic://" + name, "full_capture")
    sha = ids.sha256_bytes(data)
    a = Artifact(artifact_id=ids.artifact_id(oid, "document", sha, name), observation_id=oid,
                 role=ArtifactRole.document, local_ref="x/" + name, sha256=sha, media_type="application/json", origin=name)
    mem.d[a.artifact_id] = data
    return Observation(observation_id=oid, canonical_id=ids.canonical_id([("landing_html", sha)]), source_ref="synthetic://" + name,
                       input_kind=InputKind.creative_only, artifacts=[a], tags=["synthetic"],
                       capture_time="2026-11-01T10:00:00Z")


def rec(i, page, payer, ben, **kw):
    r = {"id": i, "page_id": "1" + i, "page_name": page, "ad_delivery_start_time": "2026-10-01",
         "ad_delivery_stop_time": "2026-10-20"}
    if payer:
        r["beneficiary_payers"] = [{"payer": payer, "beneficiary": ben, "current": True}]
    r.update(kw)
    return r


def run(*recs):
    mem, ex = Mem(), PayerExtractor()
    obs, feats = [], {}
    for n, r in enumerate(recs):
        o = ad(f"ad{n}.json", r, mem)
        f, s = ex.extract(o, reader=mem)
        obs.append(o)
        feats[o.observation_id] = f
        assert all(x.state is ExtractionState.ok for x in s)
    return obs, feats


def kinds(feats, k):
    return [f.value for f in feats if f.kind == k]


def test_registered():
    assert {"source-payer-extractor", "source-payer-compare"} <= set(discover())
    assert discover(AdapterKind.comparator)["source-payer-compare"].spec.kind is AdapterKind.comparator


def test_extract_fields_and_provenance():
    obs, feats = run(rec("101", "Sklep Marka", "Agencja Zeta Sp. z o.o.", "Klient A S.A."))
    f = feats[obs[0].observation_id]
    assert kinds(f, "payer_name") == ["Agencja Zeta Sp. z o.o."]
    assert kinds(f, "beneficiary_name") == ["Klient A S.A."]
    assert kinds(f, "advertiser_name") == ["Sklep Marka"] and kinds(f, "ad_id") == ["101"]
    assert kinds(f, "delivery_start") == ["2026-10-01"] and kinds(f, "delivery_stop") == ["2026-10-20"]
    prov = kinds(f, "record_provenance")[0]
    assert prov["repository"] == "meta-ad-library" and prov["retrieved_at"] == "2026-11-01T10:00:00Z" and prov["payer_declared"]
    pf = [x for x in f if x.kind == "payer_name"][0]
    assert pf.evidence_refs[0].locator is not None
    Bundle(observations=obs, features=[x for v in feats.values() for x in v])


def test_fallback_bylines_and_data_wrapper():
    mem = Mem()
    o = ad("w.json", None, mem, json.dumps({"data": [{"id": "5", "bylines": "Paid for by Fundacja Iks"}]}).encode())
    f, _ = PayerExtractor().extract(o, reader=mem)
    assert kinds(f, "payer_name") == ["Fundacja Iks"]
    assert "bylines" in kinds(f, "record_provenance")[0]["fields"]


def test_same_payer_different_creatives():
    obs, feats = run(rec("1", "A", "Firma X", "Firma X"), rec("2", "B", "firma x sp. z o.o.", "Firma X"),
                     rec("3", "C", "Inna Firma", "Inna Firma"))
    [pe] = PayerComparator().compare(obs[0], obs[1], feats, run_id="r1")
    assert pe.score == 1.0 and pe.relation.value == "campaign_candidate"
    g = group_by_payer(feats)
    assert sorted(g[normalize_entity("Firma X")]) == sorted([obs[0].observation_id, obs[1].observation_id])
    [pe2] = PayerComparator().compare(obs[0], obs[2], feats, run_id="r1")
    assert pe2.score == 0.0


def test_agency_misleading_match():
    obs, feats = run(rec("1", "Marka1", "Agencja Zeta", "Klient A"), rec("2", "Marka2", "Agencja Zeta", "Klient B"),
                     rec("3", "Marka1", "Agencja Zeta", "Klient A"))
    [pe] = PayerComparator().compare(obs[0], obs[1], feats, run_id="r1")
    assert pe.score == 0.4 and "agencji" in pe.explanation
    [same] = PayerComparator().compare(obs[0], obs[2], feats, run_id="r1")
    assert same.score == 1.0
    assert len(group_by_payer(feats)["agencja zeta"]) == 3  # agencja grupuje wszystkich klientów


def test_missing_payer():
    obs, feats = run(rec("1", "A", None, None), rec("2", "B", "Firma X", "Firma X"))
    mem = Mem()
    o = ad("n.json", rec("1", "A", None, None), mem)
    f, s = PayerExtractor().extract(o, reader=mem)
    assert not kinds(f, "payer_name") and "nie ujawnia" in s[0].detail
    [pe] = PayerComparator().compare(obs[0], obs[1], feats, run_id="r1")
    assert pe.score is None and not group_by_payer({obs[0].observation_id: feats[obs[0].observation_id]})


def test_bad_inputs():
    mem, ex = Mem(), PayerExtractor()
    o = ad("bad.json", None, mem, b"{nie json")
    assert ex.extract(o, reader=mem)[1][0].state is ExtractionState.failed
    o = ad("many.json", None, mem, b'{"data":[{"id":"1"},{"id":"2"}]}')
    assert ex.extract(o, reader=mem)[1][0].state is ExtractionState.unsupported
    o = ad("none.json", None, mem, b"{}")
    f, s = ex.extract(o, reader=mem)
    assert s[0].state is ExtractionState.ok and not kinds(f, "payer_name")


def test_normalize():
    assert normalize_entity("Łódź Media Sp. z o.o.") == normalize_entity("lodz media")
