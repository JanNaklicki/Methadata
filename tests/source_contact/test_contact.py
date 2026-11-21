import json

import pytest

from methadata.contracts import ids
from methadata.contracts.interfaces import AdapterKind
from methadata.contracts.models import Artifact, ArtifactRole, Bundle, ExtractionState, InputKind, Observation
from methadata.contracts.registry import discover
from methadata.sources.contact import ContactComparator, ContactExtractor, group_by_contact
from methadata.sources.contact import normalize as N


class Mem:
    def __init__(self):
        self.d = {}

    def open_bytes(self, ref):
        return self.d[ref.artifact_id]


def obs(name, body, mem, role=ArtifactRole.landing_html, media="text/html", ext=".html"):
    data = body.encode()
    oid = ids.observation_id("synthetic://" + name, "full_capture")
    sha = ids.sha256_bytes(data)
    a = Artifact(artifact_id=ids.artifact_id(oid, role.value, sha, name + ext), observation_id=oid, role=role,
                 local_ref="x/" + name + ext, sha256=sha, media_type=media, origin=name + ext)
    mem.d[a.artifact_id] = data
    return Observation(observation_id=oid, canonical_id=ids.canonical_id([("landing_html", sha)]),
                       source_ref="synthetic://" + name, input_kind=InputKind.creative_only, artifacts=[a],
                       tags=["synthetic"], capture_time="2026-11-01T10:00:00Z")


def page(brand, footer):
    return f"<html><head><title>Oferta | {brand}</title><script>var x='600 000 000'</script></head><body><footer>{footer}</footer></body></html>"


def run(*bodies):
    mem, ex = Mem(), ContactExtractor()
    os_, feats = [], {}
    for n, b in enumerate(bodies):
        o = obs(f"s{n}", b, mem)
        f, s = ex.extract(o, reader=mem)
        assert all(x.state is ExtractionState.ok for x in s)
        os_.append(o)
        feats[o.observation_id] = f
    return os_, feats


def vals(feats, kind):
    return [f.value for f in feats if f.kind == kind]


def test_registered():
    assert {"source-contact-extractor", "source-contact-compare"} <= set(discover())
    assert discover(AdapterKind.extractor)["source-contact-extractor"].spec.kind is AdapterKind.extractor


def test_extract_masked_roles_provenance():
    os_, feats = run(page("Marka Alfa", 'Zamówienia: <a href="tel:+48 500 123 456">500 123 456</a> '
                                         'e-mail: zamowienia@alfa-shop.pl Infolinia: 22 123 45 67 '
                                         '<a href="https://wa.me/48600700800">Napisz</a> NIP 123-456-78-90'))
    f = feats[os_[0].observation_id]
    ph = vals(f, "contact_phone")
    assert len(ph) == 2  # skrypt i NIP pominięte
    by_role = {p["role"]: p for p in ph}
    assert by_role["order"]["masked"] == "+48 •••••• 456" and by_role["hotline"]["masked"].endswith("567")
    assert by_role["order"]["country"] == "PL" and by_role["order"]["captured_at"] == "2026-11-01T10:00:00Z"
    assert by_role["order"]["status"] == "ok" and by_role["order"]["provenance"]["artifact_id"]
    assert vals(f, "contact_email")[0]["role"] == "order"
    assert vals(f, "contact_messenger")[0]["masked"].startswith("whatsapp:")
    assert vals(f, "contact_brand") == ["Marka Alfa"]
    blob = json.dumps([x.model_dump(mode="json") for x in f], ensure_ascii=False)
    for secret in ("500123456", "500 123 456", "zamowienia@alfa", "600700800", "221234567"):
        assert secret not in blob  # jawne wartości nie trafiają do cech
    assert [x for x in f if x.kind == "contact_phone"][0].evidence_refs[0].locator is not None
    Bundle(observations=os_, features=f)


def test_same_order_contact_different_brands():
    os_, feats = run(page("Marka Alfa", "Zamów telefonicznie: +48 (500) 123-456"),
                     page("Marka Beta", 'Zamówienia <a href="tel:0048500123456">zadzwoń</a>'),
                     page("Marka Gamma", "Zamówienia: 501 999 888"))
    [pe] = ContactComparator().compare(os_[0], os_[1], feats, run_id="r")
    assert pe.score == 0.9 and "różnymi markami" in pe.explanation and pe.relation.value == "campaign_candidate"
    assert all(pe.evidence_refs.values())
    assert ContactComparator().compare(os_[0], os_[2], feats, run_id="r")[0].score == 0.0
    g = group_by_contact(feats)
    assert sorted(next(iter(g.values()))) == sorted([os_[0].observation_id, os_[1].observation_id])


def test_same_brand_lower_and_shared_hotline_misleading():
    os_, feats = run(page("Marka Alfa", "Zamówienia: 500 123 456"), page("Marka Alfa", "Zamówienia: 500 123 456 <b>druga reklama</b>"),
                     page("Firma Pośrednik A", "Infolinia: 22 555 66 77"), page("Firma Pośrednik B", "Infolinia: 22 555 66 77"))
    assert ContactComparator().compare(os_[0], os_[1], feats, run_id="r")[0].score == 0.8
    [pe] = ContactComparator().compare(os_[2], os_[3], feats, run_id="r")
    assert pe.score == 0.35 and "infolinia" in pe.explanation and "mylące" in pe.explanation
    assert group_by_contact(feats, roles=("hotline",))


def test_known_intermediary_key_downgrades():
    os_, feats = run(page("A", "Zamówienia: 500 123 456"), page("B", "Zamówienia: 500 123 456"))
    key = vals(feats[os_[0].observation_id], "contact_phone")[0]["key"]
    cfg = ContactComparator().cfg | {"known_intermediary_keys": [key]}
    assert ContactComparator(cfg).compare(os_[0], os_[1], feats, run_id="r")[0].score == 0.35


def test_missing_contact():
    os_, feats = run(page("A", "Brak kontaktu"), page("B", "Zamówienia: 500 123 456"))
    mem = Mem()
    o = obs("n", page("A", "nic"), mem)
    f, s = ContactExtractor().extract(o, reader=mem)
    assert not f and "nie znaleziono" in s[0].detail
    [pe] = ContactComparator().compare(os_[0], os_[1], feats, run_id="r")
    assert pe.score is None and "brak podstaw" in pe.explanation


def test_ocr_text_and_obfuscation():
    mem = Mem()
    o = obs("ocr", "ZAMOW TELEFONICZNIE\n tel. 5OO 123 456\n biuro [at] firma [dot] pl\nWhatsApp +48 601 602 603",
            mem, role=ArtifactRole.document, media="text/plain", ext=".txt")
    f, s = ContactExtractor().extract(o, reader=mem)
    assert s[0].state is ExtractionState.ok
    ph = vals(f, "contact_phone")[0]
    assert ph["source"] == "ocr" and ph["status"] == "low_confidence" and ph["role"] == "order"
    assert vals(f, "contact_email")[0]["masked"] == "b•••@f•••.pl"
    assert vals(f, "contact_messenger")[0]["masked"].startswith("whatsapp:")
    # ten sam kontakt z HTML i z OCR daje ten sam skrót
    os_, feats = run(page("A", "Zamówienia: 500 123 456"))
    assert vals(feats[os_[0].observation_id], "contact_phone")[0]["key"] == ph["key"]


def test_ocr_via_optional_texts():
    mem = Mem()
    o = obs("img", "x", mem, role=ArtifactRole.creative_image, media="image/png", ext=".png")
    aid = o.artifacts[0].artifact_id
    f, _ = ContactExtractor().extract(o, reader=mem, optional={"ocr_texts": {aid: "Zamówienia 500 123 456"}})
    assert len(vals(f, "contact_phone")) == 1


def test_bad_inputs():
    mem, ex = Mem(), ContactExtractor()
    o = obs("img", "x", mem, role=ArtifactRole.creative_image, media="image/png", ext=".png")
    assert ex.extract(o, reader=mem)[1][0].state is ExtractionState.missing_input
    o = obs("e", "   ", mem)
    assert ex.extract(o, reader=mem)[1][0].state is ExtractionState.ok
    o = obs("broken", "<div><a href='tel:+48500123456'>zamów <b>", mem)
    assert len(vals(ex.extract(o, reader=mem)[0], "contact_phone")) == 1
    o = obs("gone", "x", mem)
    del mem.d[o.artifacts[0].artifact_id]
    assert ex.extract(o, reader=mem)[1][0].state is ExtractionState.missing_input


def test_normalize_phone():
    for raw in ("+48 500 123 456", "0048500123456", "(+48) 500-123-456", "500.123.456", "500 123 456"):
        assert N.normalize_phone(raw)[0] == "+48500123456", raw
    assert N.normalize_phone("500123456") is None            # goły ciąg bez kontekstu
    assert N.normalize_phone("500123456", explicit_context=True)[0] == "+48500123456"
    assert N.normalize_phone("+49 30 1234567")[1] == "DE"
    assert N.normalize_phone("+420 601 123 456")[1] == "CZ"
    assert N.normalize_phone("111 111 111") is None and N.normalize_phone("12 345") is None
    assert N.normalize_phone("20.10.2024") is None
    assert N.normalize_phone("+48 12 345") is None
    assert N.mask_phone("+48500123456") == "+48 •••••• 456"


def test_normalize_email_and_accounts():
    assert N.normalize_email("Jan.Kowalski+promo@Gmail.com")[0] == "jankowalski@gmail.com"
    assert N.normalize_email("img@2x.png") is None
    assert N.normalize_email("mailto:Biuro@Firma.PL?subject=x")[0] == "biuro@firma.pl"
    assert N.parse_messenger_url("https://t.me/Alfa_Order") == ("telegram", "alfa_order")
    assert N.parse_messenger_url("https://wa.me/48500123456?text=hi") == ("whatsapp", "+48500123456")
    assert N.parse_messenger_url("https://api.whatsapp.com/send?phone=48500123456") == ("whatsapp", "+48500123456")
    assert N.parse_messenger_url("m.me/Sklep.Alfa") == ("messenger", "sklep.alfa")
    assert N.parse_messenger_url("https://example.com/x") is None


def test_free_mail_lowers_score():
    os_, feats = run(page("A", "Zamówienia: biuro@gmail.com"), page("B", "Zamówienia: b.i.u.r.o+x@gmail.com"))
    assert ContactComparator().compare(os_[0], os_[1], feats, run_id="r")[0].score == 0.63


def test_real_data_no_false_positives():
    """Wejście rzeczywiste: strony i zrzuty z BACKUP to lejki z formularzem, bez publicznego kontaktu handlowego."""
    from methadata.devtools.realdata import BACKUP
    from methadata.sources.contact.scan import load_config, parse_html, scan
    if not BACKUP.is_dir():
        pytest.skip("brak katalogu BACKUP")
    pages = [p for p in sorted(BACKUP.glob("p*/*.html")) if p.stat().st_size < 5_000_000]
    if not pages:
        pytest.skip("brak stron HTML")
    cfg = load_config()
    for p in pages:
        hits = scan(parse_html(p.read_bytes().decode("utf-8", "replace")), cfg, "html")
        assert not hits, f"fałszywe trafienie w {p.parent.name} (pole formularza/skrypt?)"
