import json
from pathlib import Path

import pytest
from PIL import Image

from methadata.contracts import AdapterKind, Artifact, InputKind, Observation, ids
from methadata.contracts.models import ImageRect
from methadata.contracts.registry import discover
from methadata.devtools.realdata import BACKUP
from methadata.experiments.perceptual import bench, phash as P, variants as V
from methadata.experiments.perceptual.adapters import (KeypointComparator, PerceptualExtractor, PHashComparator)
from methadata.experiments.perceptual.config import DEFAULTS
from methadata.runner import run_experiment

ROOT = Path(__file__).parents[2]


class Reader:
    def __init__(self): self.d = {}
    def open_bytes(self, ref): return self.d[ref.artifact_id]


def make(name, data, reader):
    """Observation z jednym creative_image (PNG) o podanych bajtach."""
    oid = ids.observation_id(name, "full_capture")
    sha = ids.sha256_bytes(data)
    a = Artifact(artifact_id=ids.artifact_id(oid, "creative_image", sha), observation_id=oid, role="creative_image",
                 local_ref=f"{name}.png", sha256=sha, media_type="image/png")
    reader.d[a.artifact_id] = data
    return Observation(observation_id=oid, canonical_id="can-" + name, source_ref=name,
                       input_kind=InputKind.full_capture, artifacts=[a])


def img_obs(name, im, reader):
    return make(name, V.to_bytes(im), reader)


def features_of(obs_list, reader):
    """Mapa observation_id -> cechy wyekstrahowane przez PerceptualExtractor."""
    ext = PerceptualExtractor()
    return {o.observation_id: list(ext.extract(o, reader=reader)[0]) for o in obs_list}


def ill(seed):
    return bench.synthetic_illustration(seed)


# 1. pHash ----------------------------------------------------------------------------------------

def test_phash_identical_image_hamming_zero():
    data = V.to_bytes(ill(1))
    a, b = P.phash_bytes(data), P.phash_bytes(data)
    assert a is not None and b is not None
    assert P.hamming(a, b) == 0


def test_phash_flat_image_has_no_hash():
    flat = Image.new("RGB", (640, 480), (128, 128, 128))
    assert P.phash_bytes(V.to_bytes(flat)) is None


def test_phash_jpeg_q40():
    im = ill(1)
    a, b = P.phash_bytes(V.to_bytes(im)), P.phash_bytes(V.to_bytes(V.jpeg(im, 40)))
    assert a is not None and b is not None
    assert P.hamming(a, b) <= 10


def test_phash_scale_half():
    im = ill(1)
    a, b = P.phash_bytes(V.to_bytes(im)), P.phash_bytes(V.to_bytes(V.scale(im, 0.5)))
    assert a is not None and b is not None
    assert P.hamming(a, b) <= 10


def test_phash_crop_half_is_known_failure():
    # Udokumentowane ograniczenie: globalny pHash nie wykrywa kadrowania (crop 0.5 -> hamming > 10).
    # Dlatego kadrowanie obsługują punkty ORB (test_keypoints_crop), a nie pHash.
    im = ill(1)
    cropped = V.crop(im, 0.5)[0]
    a, b = P.phash_bytes(V.to_bytes(im)), P.phash_bytes(V.to_bytes(cropped))
    assert a is not None and b is not None
    assert P.hamming(a, b) > 10


# 2. keypoints (ORB + RANSAC) --------------------------------------------------------------------

def test_keypoints_jpeg():
    im = ill(1)
    assert bench.verdict(im, V.jpeg(im, 40))["kp_hit"] is True


def test_keypoints_scale():
    im = ill(1)
    assert bench.verdict(im, V.scale(im, 0.5))["kp_hit"] is True


def test_keypoints_crop():
    base = ill(1)
    cropped = V.crop(base, 0.5)[0]
    v = bench.verdict(base, cropped)
    assert v["kp_hit"] is True
    m = v["match"]
    assert m is not None
    x, y, w, h = m.rect_a
    assert x >= 0 and y >= 0 and w > 0 and h > 0
    assert x + w <= base.width and y + h <= base.height
    # różne ilustracje nie mogą dać trafienia
    assert bench.verdict(ill(1), ill(2))["kp_hit"] is False


# 3. extractor -----------------------------------------------------------------------------------

def test_extractor_illustration_has_phash_and_keypoints():
    r = Reader()
    obs = img_obs("ill", ill(1), r)
    feats, sts = PerceptualExtractor().extract(obs, reader=r)
    kinds = {f.kind for f in feats}
    assert {"phash", "keypoints"} <= kinds
    ph = next(f for f in feats if f.kind == "phash")
    assert ph.value["group"] == "illustration"
    assert ph.value["width"] == 640 and ph.value["height"] == 480
    assert all(s.state.value == "ok" for s in sts)


def test_extractor_icon_only_phash():
    r = Reader()
    obs = img_obs("icon", bench.synthetic_icon(1, 64), r)
    feats, _ = PerceptualExtractor().extract(obs, reader=r)
    assert [f.kind for f in feats] == ["phash"]
    assert feats[0].value["group"] == "icon"


def test_extractor_undecodable_is_unsupported():
    r = Reader()
    obs = make("bad", b"xx", r)
    feats, sts = PerceptualExtractor().extract(obs, reader=r)
    assert feats == []
    assert len(sts) == 1 and sts[0].state.value == "unsupported"


# 4. comparators ---------------------------------------------------------------------------------

def _pair_obs(seed=1):
    r = Reader()
    base = ill(seed)
    o_base = img_obs("base", base, r)
    o_jpeg = img_obs("jpeg", V.jpeg(base, 40), r)
    o_crop = img_obs("crop", V.crop(base, 0.5)[0], r)
    o_foreign = img_obs("foreign", ill(2), r)
    return r, o_base, o_jpeg, o_crop, o_foreign


def test_phash_comparator_variants_and_foreign():
    r, base, jpg, crop, foreign = _pair_obs()
    fm = features_of([base, jpg, foreign], r)
    cmp = PHashComparator()
    ev = cmp.compare(base, jpg, fm, run_id="r")[0]
    assert ev.score is not None and ev.score > 0.5
    ev_f = cmp.compare(base, foreign, fm, run_id="r")[0]
    assert ev_f.score is not None and ev_f.score < 0.1


def test_keypoint_comparator_variants_and_foreign():
    r, base, jpg, crop, foreign = _pair_obs()
    fm = features_of([base, jpg, crop, foreign], r)
    cmp = KeypointComparator()
    assert cmp.compare(base, jpg, fm, run_id="r")[0].score > 0.5
    ev_crop = cmp.compare(base, crop, fm, run_id="r")[0]
    assert ev_crop.score > 0.5
    assert cmp.compare(base, foreign, fm, run_id="r")[0].score < 0.1


def test_keypoint_crop_evidence_refs_and_explanation():
    r, base, jpg, crop, foreign = _pair_obs()
    fm = features_of([base, crop], r)
    ev = KeypointComparator().compare(base, crop, fm, run_id="r")[0]
    refs = ev.evidence_refs
    assert set(refs) == {"can-base", "can-crop"}
    for key in ("can-base", "can-crop"):
        ref = refs[key][0]
        assert ref.locator is not None
        assert isinstance(ref.locator, ImageRect)
        assert ref.locator.kind == "image_rect"
    assert "próg" in ev.explanation


def test_icon_pair_gives_no_score():
    r = Reader()
    a = img_obs("ia", bench.synthetic_icon(1, 64), r)
    b = img_obs("ib", bench.synthetic_icon(2, 64), r)
    fm = features_of([a, b], r)
    assert PHashComparator().compare(a, b, fm, run_id="r")[0].score is None
    assert KeypointComparator().compare(a, b, fm, run_id="r")[0].score is None


# 5. false hits ----------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def false_hits():
    return bench.false_hits(n=30)


def test_false_hits_phash_rate_informational(false_hits):
    # Informacyjnie: fałszywe trafienia pHash na ikonach (nie są dowodem, bo ikony nie wchodzą do komparatorów).
    assert false_hits["icon"]["phash_false_rate"] >= 0


def test_icon_and_avatar_pairs_do_not_hit(false_hits):
    r = Reader()
    icons = [img_obs(f"i{s}", bench.synthetic_icon(s, 64), r) for s in (1, 2, 3)]
    avatars = [img_obs(f"a{s}", bench.synthetic_avatar(s, 160), r) for s in (1, 2, 3)]
    fm = features_of(icons + avatars, r)
    for group in (icons, avatars):
        a, b = group[0], group[1]
        assert PHashComparator().compare(a, b, fm, run_id="r")[0].score is None
        assert KeypointComparator().compare(a, b, fm, run_id="r")[0].score is None


def test_illustration_keypoint_false_hits_zero(false_hits):
    assert false_hits["illustration"]["kp_false_hits"] == 0


# 6. config --------------------------------------------------------------------------------------

def test_config_file_matches_defaults():
    data = json.loads((ROOT / "configs/perceptual/perceptual.json").read_text(encoding="utf-8"))
    assert data == DEFAULTS


# 7. discovery and runner ------------------------------------------------------------------------

def test_discover_and_runner():
    found = discover()
    assert "perceptual-extractor" in found
    assert "perceptual-phash-v1" in found
    assert "perceptual-keypoints-v1" in found

    r = Reader()
    base = ill(1)
    obs = [img_obs("base", base, r),
           img_obs("crop", V.crop(base, 0.5)[0], r),
           img_obs("foreign", ill(2), r)]
    cl = next(v for v in found.values() if v.spec.kind is AdapterKind.clusterer)
    cfg = {"dataset_version": "t", "seed": 0, "relation": "shared_template", "clusterer": cl.spec.id,
           "extractors": ["perceptual-extractor"], "comparators": ["perceptual-keypoints-v1"],
           "cluster_config": {"threshold": 0.5, "similarity_version": "perc-1", "dataset_version": "t"}}
    res = run_experiment(cfg, obs, found, reader=r)
    sc = {frozenset(p.pair): p.score for p in res["bundle"].pairs}
    assert sc[frozenset({"can-base", "can-crop"})] > 0.5
    assert sc[frozenset({"can-base", "can-foreign"})] < 0.1


# 8. dane rzeczywiste (próbka kontrolna, osobno; pomijana bez katalogu BACKUP) ----------------------

def test_real_data_control_sample():
    # Próbka kontrolna: pierwszy PNG z BACKUP (nie jest częścią syntetycznego pomiaru).
    if not BACKUP.is_dir():
        pytest.skip("brak katalogu BACKUP")
    files = sorted(BACKUP.rglob("*.png"))
    if not files:
        pytest.skip("brak plików PNG w BACKUP")
    with Image.open(files[0]) as im:
        base = im.convert("RGB")
        jpg = V.jpeg(base, 40)
        crop = V.crop(base, 0.5)[0]
    assert bench.verdict(base, jpg)["kp_hit"] is True
    assert bench.verdict(base, crop)["kp_hit"] is True
