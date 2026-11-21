import os

import pytest

from methadata.contracts import ids
from methadata.contracts.interfaces import AdapterKind
from methadata.contracts.models import Artifact, ArtifactRole, Bundle, ExtractionState, InputKind, Observation
from methadata.contracts.registry import discover
from methadata.devtools.realdata import BACKUP
from methadata.sources.payee import PayeeComparator, PayeeExtractor, find_payees, group_by_payee, iban_valid

# wyłącznie syntetyczne wartości z poprawną sumą kontrolną
IBAN_PL = "PL60109024020000000000000000"
IBAN_PL_SPACED = "PL60 1090 2402 0000 0000 0000 0000"
IBAN_DE = "DE89370400440532013000"
TRON = "TA4Y62o6YC2Zsck9rZVGTvqW1AQ7X9zTnj"
BTC = "1TZbknLbwNCGMAQ4iL4MC9A58Kvj65aFZ"
EVM = "0x" + "ab" * 20


@pytest.fixture(autouse=True)
def key(monkeypatch):
    monkeypatch.setenv("METHADATA_PII_KEY", "test-key-1")


class Mem:
    def __init__(self):
        self.d = {}

    def open_bytes(self, ref):
        return self.d[ref.artifact_id]


def page(name, text, mem):
    data = (text + f"\n<!-- {name} -->").encode()
    oid = ids.observation_id("synthetic://" + name, "full_capture")
    sha = ids.sha256_bytes(data)
    a = Artifact(artifact_id=ids.artifact_id(oid, "document", sha, name), observation_id=oid, role=ArtifactRole.document,
                 local_ref="x/" + name, sha256=sha, media_type="text/html", origin=name)
    mem.d[a.artifact_id] = data
    return Observation(observation_id=oid, canonical_id=ids.canonical_id([("landing_html", sha), ("name", ids.sha256_bytes(name.encode()))]),
                       source_ref="synthetic://" + name, input_kind=InputKind.creative_only, artifacts=[a],
                       tags=["synthetic"], capture_time="2026-11-01T10:00:00Z")


def run(*texts):
    mem, ex = Mem(), PayeeExtractor()
    obs, feats = [], {}
    for n, t in enumerate(texts):
        o = page(f"p{n}.html", t, mem)
        f, s = ex.extract(o, reader=mem)
        assert all(x.state is ExtractionState.ok for x in s)
        obs.append(o)
        feats[o.observation_id] = f
    return obs, feats


def cmp(obs, feats):
    return PayeeComparator().compare(obs[0], obs[1], feats, run_id="r")[0]


def test_registered():
    assert {"source-payee-extractor", "source-payee-compare"} <= set(discover())


def test_validators():
    assert iban_valid(IBAN_PL) and iban_valid(IBAN_DE) and not iban_valid("PL61109024020000000000000000")
    sp = {s for s, *_ in find_payees(f"x {IBAN_PL_SPACED} {EVM} {TRON} {BTC} 0x1234 TAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")}
    assert sp == {"iban", "crypto:evm", "crypto:tron", "crypto:btc"}


def test_success_masked_and_provenance():
    obs, feats = run(f"Wpłać na konto {IBAN_PL_SPACED}", f"Przelew: {IBAN_PL}")
    f = feats[obs[0].observation_id][0]
    v = f.value
    assert v["space"] == "iban" and v["masked"] != IBAN_PL and IBAN_PL not in str(v) and "1090" not in str(v)
    assert v["retrieved_at"] == "2026-11-01T10:00:00Z" and v["status"] == "extracted" and v["key_id"]
    assert f.evidence_refs[0].locator is not None
    Bundle(observations=obs, features=[x for v in feats.values() for x in v])
    e = cmp(obs, feats)
    assert e.score == 1.0 and IBAN_PL not in e.explanation
    assert list(group_by_payee(feats).values()) == [sorted(o.observation_id for o in obs)]


def test_wallet_network_spaces_not_mixed():
    obs, feats = run(f"USDT {TRON}", f"USDT {EVM}")
    assert cmp(obs, feats).score is None


def test_two_merchants_same_gateway_misleading():
    obs, feats = run("pay https://stripe.com/ acct_1AbCdEfGhIjK", "pay https://stripe.com/ acct_9ZyXwVuTsRqP")
    e = cmp(obs, feats)
    assert e.score == 0.0 and "sama bramka" in e.explanation
    obs, feats = run("pay https://paypal.com/paypalme/ShopOne", "https://paypal.com/paypalme/shopone")
    assert cmp(obs, feats).score == 1.0


def test_gateway_name_only_is_no_match():
    obs, feats = run("Płacimy przez stripe.com", "Płacimy przez stripe.com")
    assert cmp(obs, feats).score is None


def test_missing_data_and_no_artifact():
    obs, feats = run("brak danych płatniczych", f"konto {IBAN_DE}")
    assert cmp(obs, feats).score is None
    o = page("x.html", "x", Mem())
    o.artifacts[0].media_type, o.artifacts[0].origin, o.artifacts[0].local_ref = "image/png", "x.png", "x/x.png"
    _, st = PayeeExtractor().extract(o, reader=Mem())
    assert st[0].state is ExtractionState.missing_input


def test_different_key_not_comparable(monkeypatch):
    mem, ex = Mem(), PayeeExtractor()
    fs = []
    for k in ("k1", "k2"):
        monkeypatch.setenv("METHADATA_PII_KEY", k)
        o = page(f"{k}.html", IBAN_PL, mem)
        fs.append((o, ex.extract(o, reader=mem)[0]))
    feats = {o.observation_id: f for o, f in fs}
    assert fs[0][1][0].value["key_id"] != fs[1][1][0].value["key_id"]
    assert cmp([fs[0][0], fs[1][0]], feats).score is None


@pytest.mark.skipif(not BACKUP.is_dir(), reason="brak danych rzeczywistych")
def test_real_har_no_false_wallet_hits_and_no_crash():
    hars = sorted(BACKUP.glob("*/*.har"))[:15]
    ex, mem = PayeeExtractor(), Mem()
    for h in hars:
        o = page(h.name, h.read_text("utf-8", errors="replace"), mem)
        f, s = ex.extract(o, reader=mem)
        assert s[0].state is ExtractionState.ok
        for x in f:
            if x.kind == "payee_account":
                assert x.value["masked"] and len(x.value["token"]) == 20
