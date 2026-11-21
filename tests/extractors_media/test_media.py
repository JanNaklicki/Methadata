import struct
import zlib

import pytest

from methadata.contracts import AdapterKind, Artifact, InputKind, Observation, ids, discover
from methadata.extractors.media import MediaExtractor, probe_bytes, sample_times
from methadata.extractors.media import probe as P


def png(w, h, color_type=2):
    def ch(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    bpp = {2: 3, 6: 4}[color_type]
    raw = b"".join(b"\x00" + b"\x7f" * (w * bpp) for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + ch(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, color_type, 0, 0, 0))
            + ch(b"IDAT", zlib.compress(raw)) + ch(b"IEND", b""))


def box(t, payload):
    return struct.pack(">I4s", 8 + len(payload), t) + payload


def mp4(duration_ms=3500, w=64, h=48, audio=True):
    mvhd = box(b"mvhd", b"\0" * 4 + b"\0" * 8 + struct.pack(">II", 1000, duration_ms) + b"\0" * 80)

    def trak(handler, tw, th):
        tkhd = box(b"tkhd", b"\0" * 4 + b"\0" * 72 + struct.pack(">II", tw << 16, th << 16))
        hdlr = box(b"hdlr", b"\0" * 8 + handler + b"\0" * 12)
        return box(b"trak", tkhd + box(b"mdia", hdlr))
    moov = box(b"moov", mvhd + trak(b"vide", w, h) + (trak(b"soun", 0, 0) if audio else b""))
    return box(b"ftyp", b"isom\0\0\0\0isom") + moov


class Reader:
    def __init__(self, files):
        self.files = files

    def open_bytes(self, ref):
        return self.files[ref.artifact_id]  # KeyError = brak pliku


def make(name, items):
    """items: [(role, data|None)] -> (Observation, Reader)"""
    oid = ids.observation_id(name, "full_capture")
    arts, files = [], {}
    for i, (role, data) in enumerate(items):
        sha = ids.sha256_bytes(data if data is not None else f"missing{i}".encode())
        a = Artifact(artifact_id=ids.artifact_id(oid, role, sha, str(i)), observation_id=oid, role=role,
                     local_ref=f"{name}/{i}", sha256=sha)
        arts.append(a)
        if data is not None:
            files[a.artifact_id] = data
    o = Observation(observation_id=oid, canonical_id="can-" + name, source_ref=name,
                    input_kind=InputKind.full_capture, artifacts=arts)
    return o, Reader(files)


def by_kind(feats, kind):
    return [f for f in feats if f.kind == kind]


def test_discovered_via_entry_point():
    assert "media-extractor" in discover(AdapterKind.extractor)


def test_icon_and_graphic():
    o, r = make("i", [("creative_image", png(32, 32, 6)), ("creative_image", png(400, 300))])
    feats, st = MediaExtractor().extract(o, reader=r)
    assert [s.state.value for s in st] == ["ok", "ok"]
    roles = [f.value for f in by_kind(feats, "role")]
    assert roles == ["icon", "graphic"]
    dims = [f.value for f in by_kind(feats, "dimensions")]
    assert dims == [{"width": 32, "height": 32}, {"width": 400, "height": 300}]
    assert by_kind(feats, "metadata")[0].value["has_alpha"] is True
    assert all(f.evidence_refs[0].artifact_id for f in feats)


def test_photo_jpeg_with_exif():
    jpg = (b"\xff\xd8" + b"\xff\xe1" + struct.pack(">H", 8) + b"Exif\0\0"
           + b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, 600, 800, 3) + b"\0" * 10)
    o, r = make("p", [("creative_image", jpg)])
    feats, _ = MediaExtractor().extract(o, reader=r)
    assert by_kind(feats, "role")[0].value == "photo"
    assert by_kind(feats, "dimensions")[0].value == {"width": 800, "height": 600}
    assert by_kind(feats, "metadata")[0].value["has_exif"] is True


def test_svg_and_unknown():
    svg = b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 16"></svg>'
    assert probe_bytes(svg).width == 24 and probe_bytes(svg).height == 16
    o, r = make("u", [("resource", b"hello world")])
    _, st = MediaExtractor().extract(o, reader=r)
    assert st[0].state.value == "ok" and "brak zasobów" in st[0].detail
    o, r = make("u2", [("creative_image", b"garbage bytes")])
    _, st = MediaExtractor().extract(o, reader=r)
    assert st[0].state.value == "unsupported"


def test_video_frames_deterministic():
    o, r = make("v", [("video", mp4())])
    ex = MediaExtractor(interval_ms=1000, max_frames=10)
    f1, st = ex.extract(o, reader=r)
    f2, _ = ex.extract(o, reader=r)
    assert f1 == f2
    assert st[0].state.value == "ok"
    (fr,) = by_kind(f1, "video_frames")
    assert [x["time_ms"] for x in fr.value["frames"]] == [0, 1000, 2000, 3000]
    assert [r_.locator.frame_ms for r_ in fr.evidence_refs] == [0, 1000, 2000, 3000]
    assert by_kind(f1, "dimensions")[0].value == {"width": 64, "height": 48}
    (au,) = by_kind(f1, "audio_track")
    assert au.evidence_refs[0].artifact_id == o.artifacts[0].artifact_id
    f3, _ = MediaExtractor(interval_ms=2000).extract(o, reader=r)
    assert [x["time_ms"] for x in by_kind(f3, "video_frames")[0].value["frames"]] == [0, 2000]


def test_video_without_audio_has_no_audio_feature():
    o, r = make("va", [("video", mp4(audio=False))])
    feats, _ = MediaExtractor().extract(o, reader=r)
    assert not by_kind(feats, "audio_track")


def test_missing_video_entry():
    o, r = make("m", [("video", None), ("creative_image", png(10, 10))])
    feats, st = MediaExtractor().extract(o, reader=r)
    assert st[0].state.value == "missing_input" and st[0].artifact_id == o.artifacts[0].artifact_id
    assert st[1].state.value == "ok"
    assert not by_kind(feats, "video_frames")


def test_sample_times():
    assert sample_times(0, 1000, 5) == []
    assert sample_times(10_000, 1000, 3) == [0, 1000, 2000]


class Store:
    def __init__(self, oid):
        self.oid, self.n = oid, 0

    def put_derived(self, *, observation_id, role, data, media_type, parent, parent_relation, origin, url=None):
        role = role.value
        self.n += 1
        assert data.startswith(b"\x89PNG")
        sha = ids.sha256_bytes(data)
        return Artifact(artifact_id=ids.artifact_id(self.oid, role, sha, origin), observation_id=self.oid, role=role,
                        local_ref=f"d/{self.n}.png", sha256=sha, parent=parent,
                        parent_relation=parent_relation, origin=origin)


@pytest.mark.skipif(not P.ffmpeg_available(), reason="brak ffmpeg")
def test_frame_decoding_with_real_video(tmp_path):
    import subprocess
    out = tmp_path / "c.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=d=2:s=64x48:r=10", "-f", "lavfi", "-i",
                    "sine=d=2", "-shortest", "-pix_fmt", "yuv420p", str(out)], check=True)
    o, r = make("rv", [("video", out.read_bytes())])
    feats, st = MediaExtractor().extract(o, reader=r, optional={"store": Store(o.observation_id)})
    assert st[0].state.value == "ok"
    assert all("artifact_id" in f for f in by_kind(feats, "video_frames")[0].value["frames"])
    assert by_kind(feats, "audio_track")


def test_dhash_stable():
    PIL = pytest.importorskip("PIL")
    from PIL import Image
    import io
    b = io.BytesIO()
    Image.linear_gradient("L").convert("RGB").save(b, "PNG")
    o, r = make("h", [("creative_image", b.getvalue())])
    f1, _ = MediaExtractor().extract(o, reader=r)
    f2, _ = MediaExtractor().extract(o, reader=r)
    assert by_kind(f1, "dhash")[0].value == by_kind(f2, "dhash")[0].value
