import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

from methadata.contracts import ids  # noqa: E402
from methadata.contracts.interfaces import AdapterKind  # noqa: E402
from methadata.contracts.models import (Artifact, ArtifactRole, ExtractionState, ImageRect, InputKind,  # noqa: E402
                                        Observation)
from methadata.contracts.registry import discover  # noqa: E402
from methadata.devtools.realdata import BACKUP  # noqa: E402
from methadata.sources.qr import QrComparator, QrExtractor, classify  # noqa: E402


class Mem:
    def __init__(self):
        self.d = {}

    def open_bytes(self, ref):
        return self.d[ref.artifact_id]


def qr_img(text, scale=8, border=60, bg=255):
    q = cv2.QRCodeEncoder.create().encode(text)
    q = cv2.resize(q, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
    return cv2.copyMakeBorder(q, border, border, border, border, cv2.BORDER_CONSTANT, value=bg)


def flyer(text, tilt=True):
    """Ulotka: kod QR na jasnym tle, widziany pod kątem (transformacja perspektywiczna)."""
    q = qr_img(text, scale=7, border=50)
    canvas = np.full((700, 900), 235, np.uint8)
    h, w = q.shape
    canvas[100:100 + h, 150:150 + w] = q
    if not tilt:
        return canvas
    src = np.float32([[0, 0], [900, 0], [900, 700], [0, 700]])
    dst = np.float32([[80, 40], [820, 110], [860, 640], [30, 690]])
    return cv2.warpPerspective(canvas, cv2.getPerspectiveTransform(src, dst), (900, 700), borderValue=200)


def png(img):
    return cv2.imencode(".png", img)[1].tobytes()


def obs(name, mem, imgs, origin=None):
    """imgs: lista (bajty, rola, media_type, origin)."""
    src = "synthetic://" + name
    oid = ids.observation_id(src, "full_capture")
    arts = []
    for i, (data, role, mt, org) in enumerate(imgs):
        sha = ids.sha256_bytes(data)
        a = Artifact(artifact_id=ids.artifact_id(oid, role.value, sha, f"{name}{i}"), observation_id=oid, role=role,
                     local_ref=f"x/{name}{i}", sha256=sha, media_type=mt, origin=org or f"{name}{i}")
        mem.d[a.artifact_id] = data
        arts.append(a)
    return Observation(observation_id=oid, canonical_id=ids.canonical_id([(a.role.value, a.sha256) for a in arts], url=src),
                       source_ref=src, input_kind=InputKind.creative_only, artifacts=arts, tags=["synthetic"],
                       capture_time="2026-11-01T10:00:00Z")


def extract(o, mem):
    f, s = QrExtractor().extract(o, reader=mem)
    return f, s


def image_obs(name, mem, img, origin=None, role=ArtifactRole.creative_image):
    return obs(name, mem, [(png(img), role, "image/png", origin)])


def cmp(oa, fa, ob, fb):
    (e,) = QrComparator().compare(oa, ob, {oa.observation_id: fa, ob.observation_id: fb}, run_id="r1")
    return e


def test_discover_returns_adapters():
    assert {"source-qr-extractor", "source-qr-compare"} <= set(discover())
    assert discover(AdapterKind.comparator)["source-qr-compare"].spec.kind is AdapterKind.comparator


def test_flat_image_decodes_with_rect():
    mem = Mem()
    o = image_obs("flat", mem, qr_img("https://shop.example/promo?utm_source=ad&id=7"))
    f, s = extract(o, mem)
    assert all(x.state is ExtractionState.ok for x in s)
    (ft,) = f
    v = ft.value
    assert v["type"] == "url" and v["target"] == "shop.example/promo?id=7" and not v["shortener"]
    r = v["rect"]
    assert r["width"] > 100 and r["height"] > 100 and v["frame_ms"] is None
    assert isinstance(ft.evidence_refs[0].locator, ImageRect)
    assert v["captured_at"] == "2026-11-01T10:00:00Z" and v["provenance"]["artifact_id"]


def test_tilted_flyer_photo_decodes():
    mem = Mem()
    o = image_obs("tilt", mem, flyer("https://shop.example/promo", tilt=True), role=ArtifactRole.landing_screenshot)
    f, _ = extract(o, mem)
    assert [x.value["target"] for x in f] == ["shop.example/promo"]


def test_frame_time_from_origin():
    mem = Mem()
    o = image_obs("fr", mem, qr_img("https://shop.example/promo"), origin="frame@3000ms", role=ArtifactRole.other)
    # rola other + image/png: obraz jest rozpoznawany po media_type
    f, _ = extract(o, mem)
    assert f[0].value["frame_ms"] == 3000 and f[0].evidence_refs[0].locator.frame_ms == 3000


def test_flyer_and_video_frames_same_target_strong():
    mem = Mem()
    of = image_obs("ulotka", mem, flyer("https://bit.ly/Ab12Cd?utm_medium=print"))
    frames = [(png(qr_img("https://www.bit.ly/Ab12Cd/")), ArtifactRole.other, "image/png", f"frame@{t}ms") for t in (1000, 2000)]
    ov = obs("film", mem, frames)
    ff, _ = extract(of, mem)
    fv, _ = extract(ov, mem)
    assert len(fv) == 1 and fv[0].value["frames_ms"] == [1000, 2000]  # ten sam cel w kilku klatkach scalony
    e = cmp(of, ff, ov, fv)
    assert e.score == pytest.approx(0.95) and "ten sam cel" in e.explanation


def test_shared_shortener_host_is_weak_and_misleading():
    mem = Mem()
    oa = image_obs("a", mem, qr_img("https://bit.ly/AAAA"))
    ob = image_obs("b", mem, qr_img("https://bit.ly/BBBB"))
    fa, fb = extract(oa, mem)[0], extract(ob, mem)[0]
    assert fa[0].value["shortener"]
    e = cmp(oa, fa, ob, fb)
    assert e.score <= 0.05 and "mylące" in e.explanation


def test_two_targets_same_regular_host_low_but_above_shortener():
    mem = Mem()
    oa = image_obs("a", mem, qr_img("https://shop.example/a"))
    ob = image_obs("b", mem, qr_img("https://shop.example/b"))
    e = cmp(oa, extract(oa, mem)[0], ob, extract(ob, mem)[0])
    assert 0.05 < e.score < 0.5 and "różne cele" in e.explanation


def test_different_hosts_zero():
    mem = Mem()
    oa = image_obs("a", mem, qr_img("https://one.example/x"))
    ob = image_obs("b", mem, qr_img("https://two.example/x"))
    assert cmp(oa, extract(oa, mem)[0], ob, extract(ob, mem)[0]).score == 0.0


def test_no_qr_is_missing_data_not_negative():
    mem = Mem()
    blank = np.full((300, 300), 255, np.uint8)
    oa = image_obs("blank", mem, blank)
    ob = image_obs("b", mem, qr_img("https://one.example/x"))
    fa, sa = extract(oa, mem)
    assert fa == [] and sa[0].state is ExtractionState.ok and "nie znaleziono" in sa[0].detail
    e = cmp(oa, fa, ob, extract(ob, mem)[0])
    assert e.score is None


def test_no_image_and_corrupt_image():
    mem = Mem()
    o = obs("bad", mem, [(b"not an image", ArtifactRole.creative_image, "image/png", None)])
    f, s = extract(o, mem)
    assert f == [] and s[0].state is ExtractionState.failed
    o2 = obs("html", mem, [(b"<html>", ArtifactRole.landing_html, "text/html", None)])
    assert extract(o2, mem)[1][0].state is ExtractionState.missing_input


def test_classify_types():
    assert classify("WIFI:T:WPA;S:MyNet;P:x;;").type == "wifi"
    assert classify("tel:+48 600-100-200").value == "tel:+48600100200"
    assert classify("mailto:A@B.pl?subject=x").value == "mailto:a@b.pl"
    assert classify("bitcoin:abc?amount=1").type == "payment"
    assert classify("hello world").type == "text"
    assert classify("   ") is None
    assert classify("example.com/x/").value == "example.com/x"
    t = classify("HTTPS://WWW.Shop.Example:443/a/?b=2&a=1&fbclid=zzz#frag")
    assert t.value == "shop.example/a?a=1&b=2"


def test_video_file_roundtrip_or_skip(tmp_path):
    p = tmp_path / "v.avi"
    w = cv2.VideoWriter(str(p), cv2.VideoWriter_fourcc(*"MJPG"), 5.0, (460, 460))
    if not w.isOpened():
        pytest.skip("cv2.VideoWriter niedostępny")
    img = cv2.cvtColor(qr_img("https://shop.example/film"), cv2.COLOR_GRAY2BGR)
    img = cv2.resize(img, (460, 460))
    for _ in range(15):
        w.write(img)
    w.release()
    mem = Mem()
    o = obs("vid", mem, [(p.read_bytes(), ArtifactRole.video, "video/x-msvideo", "v.avi")])
    f, s = extract(o, mem)
    if s[0].state is ExtractionState.failed:
        pytest.skip("brak kodeka wideo w cv2: " + str(s[0].detail))
    assert f and f[0].value["target"] == "shop.example/film" and f[0].value["frames_ms"][0] == 0


@pytest.mark.skipif(not BACKUP.is_dir(), reason="brak katalogu BACKUP")
def test_real_backup_images_do_not_crash():
    imgs = [p for p in sorted(BACKUP.rglob("*")) if p.suffix.lower() in (".png", ".jpg", ".jpeg") and p.stat().st_size < 3_000_000][:40]
    if not imgs:
        pytest.skip("brak obrazów w BACKUP")
    mem = Mem()
    for p in imgs:
        o = obs("real", mem, [(p.read_bytes(), ArtifactRole.creative_image, "image/png", p.name)])
        f, s = extract(o, mem)
        assert s[0].state in (ExtractionState.ok, ExtractionState.failed)
        for x in f:
            assert x.value["target"]
