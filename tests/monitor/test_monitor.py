import json
import os

import pytest

from methadata.ingest import Store
from methadata.monitor import JsonFileProvider, MetaAdLibraryProvider, Page, ProviderError, StateFile, payer_analyzer, run_once
from methadata.monitor.__main__ import main
from methadata.monitor.state import LockedError


def ad(i, page="Strona", payer=None, ben=None, **kw):
    r = {"id": str(i), "page_id": "9" + str(i), "page_name": page, "ad_delivery_start_time": "2026-10-01"}
    if payer:
        r["beneficiary_payers"] = [{"payer": payer, "beneficiary": ben or "B"}]
    r.update(kw)
    return r


class Seq:
    """Dostawca w pamięci: lista rekordów, kursor = offset; opcjonalne awarie na zadanych wywołaniach."""
    source_id = "test-lib"

    def __init__(self, recs, fail=None):
        self.recs, self.fail, self.calls = list(recs), dict(fail or {}), 0

    def fetch(self, cursor, limit):
        self.calls += 1
        if self.calls in self.fail:
            raise self.fail[self.calls]
        s = int(cursor or 0)
        c = self.recs[s:s + limit]
        return Page(c, str(s + len(c)) if s + len(c) < len(self.recs) else None, "2026-11-01T10:00:00Z")


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "store")


def test_two_pages_duplicate_update_and_cursor(store):
    p = Seq([ad(1, payer="Agencja X", ben="K1"), ad(2, payer="Agencja X", ben="K2"), ad(3, payer="Inny")])
    r1 = run_once(p, store, page_size=2, max_pages=1)
    assert (r1.new, r1.pages, r1.exhausted, r1.cursor) == (2, 1, False, "2")
    r2 = run_once(p, store, page_size=2, max_pages=1)
    assert (r2.new, r2.exhausted) == (1, True)
    assert len(store.observation_ids()) == 3
    # duplikat: ten sam eksport od początku nic nie zmienia
    p2 = Seq(p.recs)
    sf = StateFile(store.root, "test-lib")
    st = sf.load(); st.cursor = None; sf.save(st)
    r3 = run_once(p2, store, page_size=10)
    assert (r3.new, r3.updated, r3.duplicate) == (0, 0, 3)
    # aktualizacja: zmieniona treść rekordu -> nowa wersja, ten sam observation_id
    st = sf.load(); st.cursor = None; sf.save(st)
    p3 = Seq([ad(1, payer="Agencja X", ben="K1", ad_delivery_stop_time="2026-11-01"), *p.recs[1:]])
    r4 = run_once(p3, store, page_size=10)
    assert (r4.new, r4.updated, r4.duplicate) == (0, 1, 2)
    assert len(store.observation_ids()) == 3
    assert sum(len(store.versions(o)) for o in store.observation_ids()) == 4


def test_interrupted_run_does_not_lose_records(store):
    recs = [ad(i, payer="P") for i in range(1, 7)]
    p = Seq(recs, fail={2: ProviderError("zerwane", transient=False)})
    r = run_once(p, store, page_size=2, max_pages=5, retries=0)
    assert not r.ok and r.pages == 1 and r.cursor == "2"      # kursor tylko po zapisanej stronie
    assert len(store.observation_ids()) == 2
    r = run_once(Seq(recs), store, page_size=2, max_pages=5)  # wznowienie od kursora
    assert r.ok and r.new == 4 and r.exhausted
    assert len(store.observation_ids()) == 6


def test_crash_mid_page_replays_page_idempotently(store, monkeypatch):
    recs = [ad(i, payer="P") for i in range(1, 5)]
    from methadata.ingest.store import Store as S
    orig, n = S.save_observation, {"n": 0}

    def flaky(self, obs, import_id):
        n["n"] += 1
        if n["n"] == 2:
            raise OSError("dysk")
        return orig(self, obs, import_id)
    monkeypatch.setattr(S, "save_observation", flaky)
    r = run_once(Seq(recs), store, page_size=4)
    assert not r.ok and r.cursor is None and "zapis rekordu" in r.error
    monkeypatch.setattr(S, "save_observation", orig)
    r = run_once(Seq(recs), store, page_size=4)
    assert r.ok and (r.new, r.duplicate) == (3, 1) and len(store.observation_ids()) == 4


def test_transient_retry_then_success(store):
    sleeps = []
    p = Seq([ad(1, payer="P")], fail={1: ProviderError("5xx"), 2: ProviderError("5xx")})
    r = run_once(p, store, retries=2, backoff_s=1.0, sleep=sleeps.append)
    assert r.ok and r.retries == 2 and sleeps == [1.0, 2.0] and r.new == 1


def test_permanent_error_keeps_cursor_and_retry_without_loss(store):
    p = Seq([ad(1)], fail={1: ProviderError("brak tokenu", transient=False)})
    r = run_once(p, store)
    assert not r.ok and r.cursor is None and store.observation_ids() == []
    assert StateFile(store.root, "test-lib").load().last_error
    assert run_once(Seq([ad(1)]), store).new == 1
    assert StateFile(store.root, "test-lib").load().last_error is None


def test_record_without_id_rejected_not_fatal(store):
    r = run_once(Seq([{"page_name": "x"}, ad(1)]), store)
    assert r.ok and r.rejected == 1 and r.new == 1


def test_analysis_called_for_new_and_changed_only_and_pending_survives_failure(store):
    seen = []

    def boom(s, ids_):
        seen.append(list(ids_))
        raise RuntimeError("analiza padła")
    recs = [ad(1, payer="A", ben="K"), ad(2, payer="A", ben="K")]
    r = run_once(Seq(recs), store, analyzer=boom)
    assert not r.ok and "analiza" in r.error and len(StateFile(store.root, "test-lib").load().pending) == 2
    ok = []
    r = run_once(Seq(recs), store, analyzer=lambda s, i: ok.append(list(i)) or {"n": len(i)})
    assert r.ok and r.analyzed == 2 and ok == [seen[0]]          # kolejka dosłana mimo braku nowych rekordów
    assert StateFile(store.root, "test-lib").load().pending == []
    ok.clear()
    run_once(Seq(recs), store, analyzer=lambda s, i: ok.append(i) or {})
    assert ok == []                                              # nic nowego: analiza nie wywołana


def test_payer_analysis_groups_changed_records(store):
    recs = [ad(1, payer="Agencja X", ben="K1"), ad(2, payer="Agencja X", ben="K1"), ad(3, payer="Inny", ben="Z")]
    r = run_once(Seq(recs), store, analyzer=payer_analyzer())
    assert r.ok and r.analysis["observations"] == 3
    groups = r.analysis["groups_touched"]
    assert len(groups) == 1 and len(groups[0]) == 2
    assert (store.root / "monitor" / "analysis" / "latest" / "run.json").is_file()


def test_json_file_provider_and_cli(tmp_path, capsys):
    exp = tmp_path / "exp"
    exp.mkdir()
    (exp / "a.json").write_text(json.dumps({"data": [ad(1, payer="P"), ad(2, payer="P")]}))
    (exp / "b.jsonl").write_text(json.dumps(ad(3, payer="P")) + "\n")
    s = tmp_path / "s"
    args = ["run", "--store", str(s), "--export", str(exp), "--name", "lib"]
    assert main(args) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["new"] == 3 and out["exhausted"]
    # drugi przebieg: kursor na końcu -> nic; dopisanie danych -> tylko nowe
    (exp / "c.json").write_text(json.dumps([ad(4, payer="P")]))
    assert main(args) == 0
    out = json.loads(capsys.readouterr().out)
    assert (out["new"], out["duplicate"]) == (1, 0)
    assert main(["status", "--store", str(s), "--name", "lib"]) == 0
    assert json.loads(capsys.readouterr().out)["totals"]["new"] == 4


def test_bad_export_is_nonzero_exit(tmp_path, capsys):
    f = tmp_path / "x.json"
    f.write_text("{nie json")
    assert main(["run", "--store", str(tmp_path / "s"), "--export", str(f), "--no-analysis"]) == 1


def test_lock_blocks_concurrent_run(store):
    sf = StateFile(store.root, "test-lib")
    with sf.lock():
        r = run_once(Seq([ad(1)]), store)
    assert not r.ok and "trwa" in r.error and store.observation_ids() == []


def test_meta_provider_offline(monkeypatch):
    calls = []
    body = {"data": [ad(1)], "paging": {"cursors": {"after": "CUR2"}, "next": "https://x"}}

    def opener(url, timeout):
        calls.append(url)
        return json.dumps(body).encode()
    p = MetaAdLibraryProvider("szukaj", opener=opener, clock=lambda: "2026-11-01T00:00:00Z")
    monkeypatch.delenv("META_AD_LIBRARY_TOKEN", raising=False)
    with pytest.raises(ProviderError) as e:
        p.fetch(None, 5)
    assert not e.value.transient
    monkeypatch.setenv("META_AD_LIBRARY_TOKEN", "sekret")
    pg = p.fetch("CUR1", 5)
    assert pg.next_cursor == "CUR2" and "after=CUR1" in calls[0] and "ads_archive" in calls[0]
    body.pop("paging")
    assert p.fetch(None, 5).next_cursor is None
    body.clear(); body["error"] = {"message": "limit", "is_transient": True}
    with pytest.raises(ProviderError) as e:
        p.fetch(None, 5)
    assert e.value.transient and "sekret" not in str(e.value)


@pytest.mark.skipif(not os.environ.get("META_AD_LIBRARY_TOKEN"), reason="brak tokenu Meta Ad Library (próba rzeczywista pominięta)")
def test_live_meta_smoke(tmp_path):
    r = run_once(MetaAdLibraryProvider("pożyczka", countries=["PL"]), tmp_path / "s", page_size=5, max_pages=1)
    assert r.ok, r.error


def test_discover_still_works():
    from methadata.contracts import discover
    assert "source-payer-extractor" in discover(strict=False)
