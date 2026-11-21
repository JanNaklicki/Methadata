import json

import pytest

from methadata.contracts import ids
from methadata.contracts.interfaces import AdapterKind
from methadata.contracts.models import Artifact, ArtifactRole, ExtractionState, InputKind, Observation
from methadata.contracts.registry import discover
from methadata.devtools.realdata import BACKUP
from methadata.ingest import Store
from methadata.intake.fetch import FetchResult
from methadata.intake.service import fetch_url
from methadata.sources.contact import ContactExtractor
from methadata.sources.history import (History, HistoryComparator, HistoryExtractor, RecordedArchive, compare_versions, add_archive,
                                       cluster_as_of, diff_snapshots, from_store, make_version, save_timeline)
from methadata.sources.history.adapter import load_config
from methadata.sources.history.archive import WaybackArchive, wayback_access_url, wayback_cdx_url

CE = ContactExtractor(key=b"k" * 32)
URL = "https://Sklep.example/promo/"
NURL = "https://sklep.example/promo"


def page(gtin="5901234123457", phone="+48 601 234 567", cta="https://pay-a.example/buy", title="Słuchawki Beta",
         pub=None):
    meta = f'<meta property="article:published_time" content="{pub}">' if pub else ""
    return (f'<html><head><title>{title}</title>{meta}'
            f'<script type="application/ld+json">{json.dumps({"@type": "Product", "name": title, "gtin13": gtin})}</script></head>'
            f'<body><h1>{title}</h1><p>Zamów pod numerem tel. {phone}</p>'
            f'<a href="{cta}">Kup teraz</a></body></html>').encode()


def mv(body, cap, known=None, src="intake", url=URL, **kw):
    return make_version(url, body, cap, known_at=known, source=src, contact_extractor=CE, **kw)


class Mem:
    def __init__(self):
        self.d = {}

    def open_bytes(self, ref):
        return self.d[ref.artifact_id]


def test_registered():
    assert {"source-history-extractor", "source-history-compare"} <= set(discover())
    assert discover(AdapterKind.comparator)["source-history-compare"].spec.kind is AdapterKind.comparator


def test_snapshot_contents_and_privacy():
    v = mv(page(pub="2026-01-02T08:00:00Z"), "2026-02-01T10:00:00Z")
    s = v.snapshot
    assert v.url == NURL and s["status"] == "ok"
    assert s["offer"]["ids"]["gtin"] == ["5901234123457"]
    assert s["cta"]["host"] == "pay-a.example" and s["cta"]["text"] == "Kup teraz"
    assert s["contacts"] and "601234567" not in json.dumps(s["contacts"])  # klucz to skrót, nie numer
    assert all(len(c["key"]) >= 16 and "601234567" not in c["key"] for c in s["contacts"])
    assert v.published_at.isoformat().startswith("2026-01-02T08:00:00")


def test_recipient_change_and_product_swap_under_constant_url():
    h = History([mv(page(), "2026-03-01T10:00:00Z"),
                 mv(page(phone="+48 722 000 111", cta="https://pay-b.example/buy"), "2026-03-10T10:00:00Z"),
                 mv(page(gtin="4006381333931", phone="+48 722 000 111", cta="https://pay-b.example/buy",
                         title="Odkurzacz Gamma"), "2026-03-20T10:00:00Z")])
    (a, b), (b2, c) = h.consecutive_changes(NURL)
    cfg = load_config()
    d1 = diff_snapshots(a.snapshot, b.snapshot, cfg)
    assert "recipient_change" in d1["labels"] and "product_swap" not in d1["labels"]
    assert d1["contact"]["added"] and d1["contact"]["removed"] and d1["cta"]["host_changed"]
    d2 = diff_snapshots(b2.snapshot, c.snapshot, cfg)
    assert d2["labels"] == ["product_swap"] and d2["offer"]["added"] and d2["offer"]["title"]["jaccard"] < 0.5
    same = diff_snapshots(a.snapshot, a.snapshot, cfg)
    assert same["unchanged"] is True and same["labels"] == []


def test_offer_as_of_moment_with_fetch_and_publication_dates():
    h = History([mv(page(pub="2026-01-02T08:00:00Z"), "2026-03-01T10:00:00Z"),
                 mv(page(gtin="4006381333931"), "2026-03-10T10:00:00Z")])
    assert h.at(NURL, "2026-02-28") is None                     # brak danych != pusta oferta
    v = h.at(NURL, "2026-03-05")
    assert v.captured_at.day == 1 and v.published_at.month == 1 and v.source == "intake"
    assert h.at(NURL, "2026-03-10T10:00:00Z").captured_at.day == 10
    assert h.at(NURL, "2026-04-01").snapshot["offer"]["ids"]["gtin"] != v.snapshot["offer"]["ids"]["gtin"]


def test_duplicate_and_late_old_version():
    old, new = page(), page(gtin="4006381333931")
    h = History()
    assert h.add(mv(new, "2026-03-10T10:00:00Z")) == "new"
    assert h.add(mv(new, "2026-03-10T10:00:00Z", known="2026-03-11T00:00:00Z")) == "duplicate"
    assert h.add(mv(old, "2026-03-01T10:00:00Z", known="2026-03-15T09:00:00Z", src="report")) == "late"
    assert len(h.versions(NURL)) == 2
    assert [v.captured_at.day for v in h.versions(NURL)] == [1, 10]            # porządek wg czasu pobrania
    # wiedza na 12.03 nie zna jeszcze starej wersji; na 16.03 już tak
    assert len(h.versions(NURL, known_by="2026-03-12")) == 1
    assert h.at(NURL, "2026-03-05", known_by="2026-03-12") is None
    assert h.at(NURL, "2026-03-05", known_by="2026-03-16").source == "report"


def test_historical_cluster_reproducible_by_day():
    A, B = "https://a.example/x", "https://b.example/y"
    Z = "https://z.example/z"
    h = History([
        mv(page(), "2026-03-01T10:00:00Z", url=A), mv(page(), "2026-03-02T10:00:00Z", url=B),
        mv(page(gtin="4006381333931", phone="+48 733 111 222", cta="https://pay-z.example/"), "2026-03-02T10:00:00Z", url=Z),
        # A zmienia odbiorcę/produkt: od 10.03 pasuje do Z
        mv(page(gtin="4006381333931", phone="+48 733 111 222", cta="https://pay-z.example/"), "2026-03-10T10:00:00Z", url=A)])
    c5 = cluster_as_of(h, "2026-03-05")
    assert c5 == [[A, B]]
    assert cluster_as_of(h, "2026-03-15") == [[A, Z]]
    # spóźnione zgłoszenie starej wersji B (inne dane) nie zmienia stanu odtworzonego na 5.03 z wiedzą z 5.03...
    before = cluster_as_of(h, "2026-03-05")
    h.add(mv(page(gtin="4006381333931", phone="+48 733 111 222", cta="https://pay-z.example/"), "2026-03-03T10:00:00Z",
             known="2026-03-20T00:00:00Z", src="report", url=B))
    assert cluster_as_of(h, "2026-03-05") == before
    # ...ale odtworzenie z wiedzą z 25.03 widzi starą wersję
    assert cluster_as_of(h, "2026-03-25") == [[A, B, Z]]
    assert cluster_as_of(h, "2026-03-05") == before


def test_archive_recorded_samples_no_network():
    arch = RecordedArchive({NURL: [{"timestamp": "20260201120000", "html": page().decode(), "access_url": "https://x/1"},
                                  {"timestamp": "20260215120000", "html": page(phone="+48 722 000 111").decode()}]})
    h = History()
    st = add_archive(h, arch, NURL, known_at="2026-04-01T00:00:00Z", contact_extractor=CE)
    assert st["new"] == 2
    assert add_archive(h, RecordedArchive({}), NURL)["new"] == 0           # brak kopii: brak błędu
    vs = h.versions(NURL)
    assert vs and all(v.source == "archive:recorded" for v in vs)
    assert len(vs) == 2 and vs[0].known_at.month == 4 and vs[0].captured_at.month == 2
    assert "recipient_change" in diff_snapshots(vs[0].snapshot, vs[1].snapshot, load_config())["labels"]
    score, why = compare_versions(vs[0], vs[1])
    assert score < 0.3 and "mylące" in why
    assert wayback_cdx_url("https://a.example/?q=1").startswith("https://web.archive.org/cdx/")
    assert wayback_access_url("https://a.example/", "20260101000000").endswith("id_/https://a.example/")
    assert WaybackArchive(fetch=lambda u: (_ for _ in ()).throw(OSError("offline"))).snapshots("https://a.example") == []


class FakeFetcher:
    def __init__(self, bodies):
        self.bodies, self.n = bodies, 0

    def fetch(self, url):
        b = self.bodies[self.n]
        self.n += 1
        return FetchResult(url=url, final_url=url, fetch_status="ok", http_status=200, body=b, content_type="text/html",
                           started_at=f"2026-03-{self.n * 10:02d}T10:00:00Z")


def test_store_versions_from_repeated_intake(tmp_path):
    store = Store(tmp_path / "s")
    f = FakeFetcher([page(), page(phone="+48 722 000 111", cta="https://pay-b.example/buy")])
    for _ in range(2):
        fetch_url("https://sklep.example/promo", store, fetcher=f)
    h = from_store(store, contact_extractor=CE)
    vs = h.versions(NURL)
    assert [v.version for v in vs] == [1, 2] and all(v.source == "intake" and v.observation_id for v in vs)
    d = diff_snapshots(vs[0].snapshot, vs[1].snapshot, load_config())
    assert "recipient_change" in d["labels"]
    assert h.at(NURL, "2026-03-15").version == 1
    art = save_timeline(store, vs[0].observation_id, h, NURL)
    data = json.loads(store.open_bytes(art.artifact_id))
    assert len(data["versions"]) == 2 and art.parent is not None


def _obs(mem, name, body, url, cap, kind="full_capture"):
    data = body
    oid = ids.observation_id(url or "synthetic://" + name, kind)
    sha = ids.sha256_bytes(data)
    a = Artifact(artifact_id=ids.artifact_id(oid, "landing_html", sha, name + ".html"), observation_id=oid,
                 role=ArtifactRole.landing_html, local_ref="x/" + name, sha256=sha, media_type="text/html", origin=name + ".html")
    mem.d[a.artifact_id] = data
    return Observation(observation_id=oid, canonical_id=ids.canonical_id([("landing_html", sha)]), source_ref=url or "synthetic://" + name,
                       input_kind=InputKind(kind), artifacts=[a], tags=["synthetic"], capture_time=cap)


KINDS = ["full_capture", "creative_only"]


def _run(pairs):
    mem, ex = Mem(), HistoryExtractor(contact_extractor=CE)
    os_, feats = [], {}
    for n, (body, url, cap) in enumerate(pairs):
        o = _obs(mem, f"p{n}", body, url, cap, KINDS[n % 2])
        f, s = ex.extract(o, reader=mem)
        assert s[0].state is ExtractionState.ok
        os_.append(o)
        feats[o.observation_id] = f
    return os_, feats


def cmp(os_, feats):
    (e,) = HistoryComparator().compare(os_[0], os_[1], feats, run_id="r1")
    return e


def test_extractor_feature_has_provenance_time_status():
    os_, feats = _run([(page(), "https://sklep.example/promo", "2026-03-01T10:00:00Z")])
    (f,) = feats[os_[0].observation_id]
    v = f.value
    assert v["status"] == "ok" and v["captured_at"] == "2026-03-01T10:00:00Z" and v["source"] == "intake"
    assert v["provenance"]["extractor"].startswith("s08-history") and v["provenance"]["sha256"] and f.evidence_refs


def test_compare_success_misleading_and_missing():
    # sukces: ten sam URL, bez zmian
    assert HistoryComparator().compare(*[_obs(Mem(), "q", page(), "https://sklep.example/promo", "2026-03-01T10:00:00Z")] * 2, {}, run_id="r") == []
    e = cmp(*_run([(page(), "https://sklep.example/promo", "2026-03-01T10:00:00Z"),
                   (page() + b"<!--v2-->", "https://sklep.example/promo", "2026-03-02T10:00:00Z")]))
    assert e.score == 1.0 and "bez zmian" in e.explanation
    # mylące dopasowanie: ten sam URL, podmieniony odbiorca
    e = cmp(*_run([(page(), "https://sklep.example/promo", "2026-03-01T10:00:00Z"),
                   (page(phone="+48 722 000 111", cta="https://pay-b.example/buy"), "https://sklep.example/promo", "2026-03-20T10:00:00Z")]))
    assert e.score < 0.3 and "mylące" in e.explanation and "zmiana odbiorcy" in e.explanation
    # różne URL-e, wspólny kontakt
    e = cmp(*_run([(page(), "https://a.example/x", "2026-03-01T10:00:00Z"), (page() + b"<!--b-->", "https://b.example/y", "2026-03-02T10:00:00Z")]))
    assert e.score == 0.8 and "contact" in e.explanation
    # brak danych: strona bez sygnałów
    e = cmp(*_run([(page(), "https://a.example/x", "2026-03-01T10:00:00Z"), (b"<html><body>x</body></html>", "https://b.example/y", "2026-03-02T10:00:00Z")]))
    assert e.score is None
    # brak artefaktu HTML
    mem = Mem()
    o = _obs(mem, "n", b"x", None, "2026-03-01T10:00:00Z")
    o = o.model_copy(update={"artifacts": [o.artifacts[0].model_copy(update={"role": ArtifactRole.creative_image, "media_type": "image/png", "origin": "a.png", "local_ref": "x/a.png"})]})
    f, s = HistoryExtractor().extract(o, reader=mem)
    assert f == [] and s[0].state is ExtractionState.missing_input


@pytest.mark.skipif(not BACKUP.is_dir(), reason="brak danych rzeczywistych")
def test_real_backup_pages_single_version_means_no_change():
    ex, mem, h, n = HistoryExtractor(contact_extractor=CE), Mem(), History(), 0
    for p in sorted(BACKUP.glob("*/*.html"))[:15]:
        o = _obs(mem, f"r{n}", p.read_bytes(), f"https://real{n}.example/", "2026-03-01T10:00:00Z")
        _, s = ex.extract(o, reader=mem)
        assert s[0].state is ExtractionState.ok
        v = make_version(f"https://real{n}.example/", p.read_bytes(), "2026-03-01T10:00:00Z", contact_extractor=CE)
        h.add(v)
        n += 1
    assert n and all(len(h.versions(u)) == 1 for u in h.urls())      # jedna wersja na stronę: brak historii do porównania
