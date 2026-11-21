"""E11: wektory wizyjne ilustracji głównej i jej wycinków; porównanie cosinusów z zachowaniem dopasowanego wycinka.

Ekstraktor `visual-embedding-extractor` zapisuje dla grafiki z grupy illustration (jak E09; ikony to grupa kontrolna)
cechę `image_embeddings` = {artifact_id, group, backend, backend_version, dim, width, height, crops:[{id, rect, vec}]}.
Gdy podano optional["captions"] = {observation_id: tekst} i backend obsługuje tekst, zapisuje też `caption_embedding`.

Komparatory (PairEvidence, relation=shared_template):
 - `visual-embedding-v1`: maks. cosinus po parach wycinków; evidence_refs z ImageRect dopasowanych wycinków.
 - `visual-caption-crossmodal-v1`: osobny wariant; średnia cos(podpis A, obraz B) i cos(podpis B, obraz A)
   w przestrzeni multimodalnej. Brak podpisu lub backendu tekstowego -> score=None."""
from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from methadata.contracts import (AdapterKind, AdapterSpec, ArtifactRef, ExtractionState, ExtractionStatus, Feature,
                                 PairEvidence, Relation, ids)
from methadata.contracts.models import ImageRect, ValueType
from methadata.experiments.perceptual.adapters import _is_image, group_of

from .backends import Backend, BackendUnavailable, make_backend
from .config import DEFAULTS
from .crops import crop_rects

EXTRACTOR_VERSION = "visual-embedding-1"
FAMILY = "visual_embedding"
KIND_IMG, KIND_CAP = "image_embeddings", "caption_embedding"


def _dot(u, v) -> float:
    return float(sum(a * b for a, b in zip(u, v)))


class VisualEmbeddingExtractor:
    spec = AdapterSpec(id="visual-embedding-extractor", kind=AdapterKind.extractor, version=EXTRACTOR_VERSION,
                       description="Wektory wizyjne ilustracji i wycinków (open_clip ViT-B-32); podpis opcjonalnie",
                       extras=["visual-embedding"])

    def __init__(self, config: Optional[Mapping[str, Any]] = None, backend: Optional[Backend] = None):
        self.cfg = {**DEFAULTS, **(config or {})}
        self._backend = backend

    def backend(self) -> Backend:
        if self._backend is None:
            self._backend = make_backend(self.cfg)
        return self._backend

    def _feat(self, oid, art, kind, value):
        return Feature(feature_id=ids.feature_id(oid, FAMILY, kind, EXTRACTOR_VERSION, [art.artifact_id, kind]),
                       observation_id=oid, family=FAMILY, kind=kind, value_type=ValueType.json, value=value,
                       extractor_version=EXTRACTOR_VERSION, evidence_refs=[ArtifactRef(artifact_id=art.artifact_id)])

    def extract(self, observation, *, reader, optional: Optional[Mapping[str, Any]] = None):
        from methadata.experiments.perceptual import phash as P
        oid = observation.observation_id
        feats: list[Feature] = []
        sts: list[ExtractionStatus] = []

        def status(art, state, detail=None):
            sts.append(ExtractionStatus(observation_id=oid, artifact_id=art.artifact_id if art else None,
                                        extractor_version=EXTRACTOR_VERSION, state=state, detail=detail))
        imgs = [a for a in observation.artifacts if _is_image(a)][: int(self.cfg["max_images"])]
        if not imgs:
            status(None, ExtractionState.ok, "brak grafik; pusty wynik to brak podstaw")
        dec = int(self.cfg["vec_decimals"])
        first = None
        for art in imgs:
            try:
                data = reader.open_bytes(ArtifactRef(artifact_id=art.artifact_id))
            except (FileNotFoundError, KeyError, OSError) as e:
                status(art, ExtractionState.missing_input, f"brak pliku {art.local_ref}: {type(e).__name__}")
                continue
            try:
                gray = P.load_gray(data) if data else None
            except ImportError as e:
                status(art, ExtractionState.unsupported, f"brak biblioteki ({e.name}); zainstaluj extra visual-embedding")
                continue
            if gray is None:
                status(art, ExtractionState.unsupported, "obraz niedekodowalny (np. SVG, uszkodzony)")
                continue
            w, h = gray.size
            group = group_of(w, h, self.cfg)
            if group != "illustration":
                status(art, ExtractionState.ok, "ikona/awatar: grupa kontrolna, bez wektorów")
                continue
            try:
                from PIL import Image
                import io
                with Image.open(io.BytesIO(data)) as im:
                    im.load()
                    rgb = im.convert("RGB")
                rects = crop_rects(w, h, self.cfg)
                be = self.backend()
                vecs = be.embed_images([rgb.crop((x, y, x + cw, y + ch)) for _, (x, y, cw, ch) in rects])
            except (BackendUnavailable, ImportError) as e:
                status(art, ExtractionState.unsupported, str(e))
                continue
            feats.append(self._feat(oid, art, KIND_IMG, {
                "artifact_id": art.artifact_id, "group": group, "backend": be.name, "backend_version": be.version,
                "dim": int(vecs.shape[1]), "width": w, "height": h,
                "crops": [{"id": n, "rect": list(r), "vec": [round(float(v), dec) for v in row]}
                          for (n, r), row in zip(rects, vecs)]}))
            first = first or art
            status(art, ExtractionState.ok, f"{len(rects)} wektorów ({be.name}/{be.version})")
        cap = ((optional or {}).get("captions") or {}).get(oid)
        if cap and first is not None:
            try:
                be = self.backend()
                v = be.embed_texts([cap])[0]
                feats.append(self._feat(oid, first, KIND_CAP, {
                    "artifact_id": first.artifact_id, "backend": be.name, "backend_version": be.version,
                    "text": cap, "vec": [round(float(x), dec) for x in v]}))
            except (BackendUnavailable, ImportError) as e:
                status(first, ExtractionState.unsupported, f"podpis pominięty: {e}")
        return feats, sts


def _imgs(features: Sequence[Feature], cfg) -> list[Mapping[str, Any]]:
    out = [f.value for f in features if f.family == FAMILY and f.kind == KIND_IMG]
    out.sort(key=lambda v: v["artifact_id"])
    return out[: int(cfg["max_images"])]


class _Base:
    relation = Relation.shared_template
    method_id = ""

    def __init__(self, config: Optional[Mapping[str, Any]] = None):
        self.cfg = {**DEFAULTS, **(config or {})}

    def _none(self, pair, run_id, why):
        return [PairEvidence(run_id=run_id, pair=pair, method_id=self.method_id, relation=self.relation,
                             score=None, explanation=why)]

    def compare(self, a, b, features, *, run_id, optional=None) -> Sequence[PairEvidence]:
        (ca, oa), (cb, ob) = sorted([(a.canonical_id, a), (b.canonical_id, b)], key=lambda t: t[0])
        pair = (ca, cb)
        fa, fb = features.get(oa.observation_id, []), features.get(ob.observation_id, [])
        res = self._best(fa, fb)
        if isinstance(res, str):
            return self._none(pair, run_id, res)
        score, refs_a, refs_b, expl = res
        return [PairEvidence(run_id=run_id, pair=pair, method_id=self.method_id, relation=self.relation,
                             score=min(1.0, max(0.0, score)), evidence_refs={ca: refs_a, cb: refs_b},
                             explanation=expl)]


def _ref(art_id, rect):
    x, y, w, h = rect
    return ArtifactRef(artifact_id=art_id, locator=ImageRect(x=x, y=y, width=w, height=h))


class VisualEmbeddingComparator(_Base):
    method_id = "visual-embedding-v1"
    spec = AdapterSpec(id=method_id, kind=AdapterKind.comparator, version="1",
                       description="Maks. cosinus wektorów wizyjnych (cała ilustracja i wycinki); zachowuje wycinek",
                       extras=["visual-embedding"])

    def _best(self, fa, fb):
        ia, ib = _imgs(fa, self.cfg), _imgs(fb, self.cfg)
        if not ia or not ib:
            return ("brak podstaw: po jednej ze stron brak wektorów ilustracji (brak grafik, ikona albo "
                    "backend niedostępny)")
        best = None
        for x in ia:
            for y in ib:
                if (x["backend"], x["backend_version"]) != (y["backend"], y["backend_version"]):
                    continue  # tylko ten sam zamrożony model
                for cx in x["crops"]:
                    for cy in y["crops"]:
                        c = _dot(cx["vec"], cy["vec"])
                        if best is None or c > best[0]:
                            best = (c, x, cx, y, cy)
        if best is None:
            return "brak podstaw: wektory pochodzą z różnych wersji modelu"
        c, x, cx, y, cy = best
        fl, hit = self.cfg["cos_floor"], self.cfg["cos_hit"]
        score = (c - fl) / (1.0 - fl)
        expl = (f"{x['backend']}/{x['backend_version']}: najlepszy cosinus {c:.3f} między wycinkiem {cx['id']} "
                f"(x={cx['rect'][0]} y={cx['rect'][1]} {cx['rect'][2]}x{cx['rect'][3]}) A a {cy['id']} "
                f"(x={cy['rect'][0]} y={cy['rect'][1]} {cy['rect'][2]}x{cy['rect'][3]}) B; próg trafienia {hit} "
                f"({'trafienie' if c >= hit else 'brak trafienia'}); wynik {score:.3f} = (cos-{fl})/(1-{fl}). "
                f"Podobieństwo kategorii/wyglądu, nie dowód tej samej oferty.")
        return (score, [_ref(x["artifact_id"], cx["rect"])], [_ref(y["artifact_id"], cy["rect"])], expl)


class CaptionCrossModalComparator(_Base):
    method_id = "visual-caption-crossmodal-v1"
    spec = AdapterSpec(id=method_id, kind=AdapterKind.comparator, version="1",
                       description="Podpis tekstowy vs obraz w modelu multimodalnym (cos tekst-obraz, krzyżowo)",
                       extras=["visual-embedding"])

    def _best(self, fa, fb):
        ia, ib = _imgs(fa, self.cfg), _imgs(fb, self.cfg)
        ta = [f.value for f in fa if f.family == FAMILY and f.kind == KIND_CAP]
        tb = [f.value for f in fb if f.family == FAMILY and f.kind == KIND_CAP]
        if not (ia and ib and ta and tb):
            return "brak podstaw: brak podpisu lub wektorów obrazu po którejś stronie (backend bez tekstu?)"
        ta, tb = ta[0], tb[0]

        def best_crop(cap, imgs):
            r = None
            for x in imgs:
                if (x["backend"], x["backend_version"]) != (cap["backend"], cap["backend_version"]):
                    continue
                for c in x["crops"]:
                    v = _dot(cap["vec"], c["vec"])
                    if r is None or v > r[0]:
                        r = (v, x, c)
            return r
        r_ab, r_ba = best_crop(ta, ib), best_crop(tb, ia)  # podpis A -> obraz B, podpis B -> obraz A
        if r_ab is None or r_ba is None:
            return "brak podstaw: podpis i obraz pochodzą z różnych wersji modelu"
        cos = (r_ab[0] + r_ba[0]) / 2
        lo, hi = self.cfg["cross_lo"], self.cfg["cross_hi"]
        score = (cos - lo) / (hi - lo)
        (_, xb, cb), (_, xa, ca) = r_ab, r_ba
        expl = (f"{ta['backend']}/{ta['backend_version']}: cos(podpis A, obraz B)={r_ab[0]:.3f} (wycinek {cb['id']}), "
                f"cos(podpis B, obraz A)={r_ba[0]:.3f} (wycinek {ca['id']}); średnia {cos:.3f}; "
                f"wynik {score:.3f} = (cos-{lo})/({hi}-{lo}). Kalibracja cosinusów tekst-obraz zależy od modelu.")
        return (score, [_ref(xa["artifact_id"], ca["rect"])], [_ref(xb["artifact_id"], cb["rect"])], expl)


def get_adapters() -> list[Any]:
    return [VisualEmbeddingExtractor(), VisualEmbeddingComparator(), CaptionCrossModalComparator()]


__all__ = ["VisualEmbeddingExtractor", "VisualEmbeddingComparator", "CaptionCrossModalComparator", "get_adapters",
           "EXTRACTOR_VERSION"]
