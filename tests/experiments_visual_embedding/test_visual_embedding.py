import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

for _m in ("numpy", "PIL"):
    pytest.importorskip(_m)
from PIL import Image  # noqa: E402

from methadata.contracts import Artifact, ExtractionState, InputKind, Observation, ids  # noqa: E402
from methadata.contracts.models import ImageRect  # noqa: E402
from methadata.contracts.registry import discover  # noqa: E402
from methadata.devtools.realdata import BACKUP  # noqa: E402
from methadata.experiments.perceptual import variants as V  # noqa: E402
from methadata.experiments.visual_embedding import (BackendUnavailable, CaptionCrossModalComparator,  # noqa: E402
                                                    ColorLayoutBackend, VisualEmbeddingComparator,
                                                    VisualEmbeddingExtractor, bench, load_config, make_backend)
from methadata.experiments.visual_embedding.backends import Backend  # noqa: E402
from methadata.experiments.visual_embedding.crops import crop_rects  # noqa: E402
from methadata.experiments.visual_embedding.config import DEFAULTS  # noqa: E402
from methadata.runner import run_experiment  # noqa: E402

ROOT = Path(__file__).parents[2]


class Reader:
    def __init__(self): self.d = {}
    def open_bytes(self, ref): return self.d[ref.artifact_id]


def make(name, im, reader):
    data = V.to_bytes(im)
    oid = ids.observation_id(name, "full_capture")
    sha = ids.sha256_bytes(data)
    a = Artifact(artifact_id=ids.artifact_id(oid, "creative_image", sha), observation_id=oid, role="creative_image",
                 local_ref=f"{name}.png", sha256=sha, media_type="image/png")
    reader.d[a.artifact_id] = data
    return Observation(observation_id=oid, canonical_id="can-" + name, source_ref=name,
                       input_kind=InputKind.full_capture, artifacts=[a])


class FakeBackend(Backend):
    """Synteza logiki: wektor zależy od średniej barwy (kategoria) - bez modelu. Tekst: z mapy słów."""
    name, version, dim, supports_text = "fake", "1", 3, True

    def embed_images(self, images):
        import numpy as np
        rows = [np.asarray(im.convert("RGB").resize((1, 1)), dtype=float).ravel() + 1e-3 for im in images]
        m = np.vstack(rows)
        return m / np.linalg.norm(m, axis=1, keepdims=True)

    def embed_texts(self, texts):
        import numpy as np
        table = {"red": [1, 0, 0], "green": [0, 1, 0]}
        return np.array([table[t] for t in texts], dtype=float)


def solid_scene(color, seed):
    """Ta sama kategoria (dominujący kolor), różna oferta (układ elementów)."""
    from PIL import ImageDraw
    im = Image.new("RGB", (640, 480), color)
    d = ImageDraw.Draw(im)
    d.rectangle([20 + seed * 30, 30, 120 + seed * 30, 90], outline=(255, 255, 255), width=3)
    return im


def feats(obs, reader, backend, optional=None):
    ext = VisualEmbeddingExtractor(backend=backend)
    return {o.observation_id: list(ext.extract(o, reader=reader, optional=optional)[0]) for o in obs}


def test_crop_rules_frozen():
    rects = crop_rects(640, 480, DEFAULTS)
    assert [n for n, _ in rects] == ["whole", "g2-tl", "g2-tr", "g2-bl", "g2-br", "center"]
    assert rects[0][1] == (0, 0, 640, 480) and dict(rects)["center"] == (93, 70, 453, 339)
    assert [n for n, _ in crop_rects(80, 80, DEFAULTS)] == ["whole"]  # wycinki < min_crop_side pominięte
    cfg = json.loads((ROOT / "configs/visual_embedding/visual_embedding.json").read_text())
    assert cfg == DEFAULTS and (cfg["model"], cfg["pretrained"]) == ("ViT-B-32", "laion2b_s34b_b79k")


def test_discover_registers_adapters():
    assert {"visual-embedding-extractor", "visual-embedding-v1", "visual-caption-crossmodal-v1"} <= set(discover())


def test_discover_without_numpy_torch():
    code = ("import sys\nfor m in ('numpy','torch','open_clip','PIL'): sys.modules[m]=None\n"
            "from methadata.contracts.registry import discover\n"
            "assert 'visual-embedding-v1' in discover()\nprint('OK')")
    r = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True)
    assert r.returncode == 0 and "OK" in r.stdout, r.stderr[-1500:]


def test_cropped_illustration_matches_with_kept_crop():
    r = Reader()
    base = bench.synthetic_category(1, 1)
    cropped, (cx, cy, cw, ch) = V.crop(base, 0.5, (0.2, 0.7))
    a, b = make("base", base, r), make("crop", cropped, r)
    be = FakeBackend()
    ev = VisualEmbeddingComparator().compare(a, b, feats([a, b], r, be), run_id="r")[0]
    assert ev.score is not None and ev.score > 0.9
    for cid in ev.pair:
        loc = ev.evidence_refs[cid][0].locator
        assert isinstance(loc, ImageRect)
    assert "wycinkiem" in ev.explanation and "fake/1" in ev.explanation


def test_same_category_different_offer_high_similarity():
    r = Reader()
    a, b = make("a", solid_scene((200, 30, 30), 1), r), make("b", solid_scene((200, 30, 30), 3), r)
    c = make("c", solid_scene((30, 200, 30), 1), r)
    f = feats([a, b, c], r, FakeBackend())
    cmp = VisualEmbeddingComparator()
    same = cmp.compare(a, b, f, run_id="r")[0].score
    diff = cmp.compare(a, c, f, run_id="r")[0].score
    assert same > 0.95 and diff < 0.3  # kategoria (kolor) taka sama mimo innej oferty


def test_icon_is_control_group_and_none_score():
    r = Reader()
    icon = make("icon", Image.new("RGB", (64, 64), (10, 200, 10)), r)
    big = make("big", bench.synthetic_category(1, 1), r)
    f = feats([icon, big], r, FakeBackend())
    assert f[icon.observation_id] == []
    ev = VisualEmbeddingComparator().compare(icon, big, f, run_id="r")[0]
    assert ev.score is None and ev.explanation


def test_model_version_mismatch_not_compared():
    r = Reader()
    a, b = make("a", solid_scene((200, 30, 30), 1), r), make("b", solid_scene((200, 30, 30), 2), r)
    fa, fb = feats([a], r, FakeBackend()), feats([b], r, ColorLayoutBackend())
    ev = VisualEmbeddingComparator().compare(a, b, {**fa, **fb}, run_id="r")[0]
    assert ev.score is None and "wersji modelu" in ev.explanation


def test_caption_crossmodal_variant():
    r = Reader()
    a, b = make("a", solid_scene((200, 30, 30), 1), r), make("b", solid_scene((210, 40, 40), 2), r)
    c = make("c", solid_scene((30, 200, 30), 2), r)
    caps = {a.observation_id: "red", b.observation_id: "red", c.observation_id: "green"}
    f = feats([a, b, c], r, FakeBackend(), {"captions": caps})
    cmp = CaptionCrossModalComparator({"cross_lo": 0.2, "cross_hi": 0.9})
    ab, ac = cmp.compare(a, b, f, run_id="r")[0], cmp.compare(a, c, f, run_id="r")[0]
    assert ab.score > 0.8 and ac.score < 0.1
    # bez podpisu -> brak podstaw
    f2 = feats([a, b], r, FakeBackend())
    assert cmp.compare(a, b, f2, run_id="r")[0].score is None


def test_unavailable_backend_gives_unsupported_status():
    class Broken(Backend):
        name, version = "broken", "0"
        def embed_images(self, images): raise BackendUnavailable("brak wag")
    r = Reader()
    a = make("a", bench.synthetic_category(1, 1), r)
    fs, sts = VisualEmbeddingExtractor(backend=Broken()).extract(a, reader=r)
    assert fs == [] and sts[0].state is ExtractionState.unsupported and "brak wag" in sts[0].detail


def test_unknown_backend_unsupported():
    with pytest.raises(BackendUnavailable):
        make_backend({**DEFAULTS, "backend": "nope"})


def test_runner_integration_color_layout():
    r = Reader()
    base = bench.synthetic_category(1, 1)
    obs = [make("base", base, r), make("crop", V.crop(base, 0.5)[0], r),
           make("foreign", bench.synthetic_category(77, 5), r)]
    found = dict(discover())
    cfgb = {"backend": "color-layout"}
    found["visual-embedding-extractor"] = VisualEmbeddingExtractor(cfgb)
    found["visual-embedding-v1"] = VisualEmbeddingComparator(cfgb)
    cl = next(v for v in found.values() if v.spec.kind.value == "clusterer")
    cfg = {"dataset_version": "t", "seed": 0, "relation": "shared_template", "clusterer": cl.spec.id,
           "extractors": ["visual-embedding-extractor"], "comparators": ["visual-embedding-v1"],
           "cluster_config": {"threshold": 0.5, "similarity_version": "ve-1", "dataset_version": "t"}}
    res = run_experiment(cfg, obs, found, reader=r)
    sc = {frozenset(p.pair): p.score for p in res["bundle"].pairs}
    assert sc[frozenset({"can-base", "can-crop"})] > 0.5
    assert sc[frozenset({"can-base", "can-foreign"})] < sc[frozenset({"can-base", "can-crop"})]


def test_bench_adds_over_phash_synthetic():
    rows = {r["scenario"]: r for r in bench.run_scenarios(ColorLayoutBackend())}
    assert rows["crop50"]["emb_hit"] and not rows["crop50"]["phash_hit"] and rows["crop50"]["adds_over_phash"]
    assert rows["jpeg_q40"]["phash_hit"] and not rows["jpeg_q40"]["adds_over_phash"]
    assert not rows["unrelated"]["emb_hit"]


def _real_clip():
    pytest.importorskip("open_clip")
    pytest.importorskip("torch")
    if not os.environ.get("METHADATA_MODEL_CACHE"):
        pytest.skip("brak METHADATA_MODEL_CACHE (wagi nie są pobierane w testach)")
    be = make_backend(DEFAULTS)
    try:
        be.embed_images([Image.new("RGB", (64, 64))])
    except BackendUnavailable as e:
        pytest.skip(str(e))
    return be


def test_real_clip_crop_and_text():
    be = _real_clip()
    rows = {r["scenario"]: r for r in bench.run_scenarios(be)}
    assert rows["crop50"]["emb_hit"] and rows["crop50"]["adds_over_phash"]
    red = be.embed_images([Image.new("RGB", (224, 224), (220, 20, 20))])
    t = be.embed_texts(["a red square", "a green square"])
    assert float((red @ t[0])[0]) > float((red @ t[1])[0])


def test_real_data_control_sample():
    if not BACKUP.is_dir() or not list(BACKUP.rglob("*.png")):
        pytest.skip("brak katalogu BACKUP / PNG")
    rows = bench.real_sample(ColorLayoutBackend(), n=1)
    assert rows and rows[0]["scenario"].endswith("crop50") and rows[0]["emb_hit"]
