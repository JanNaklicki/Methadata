from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional, Sequence

from methadata.contracts import (AdapterKind, AdapterSpec, ArtifactRef, ExtractionState, ExtractionStatus, Feature,
                                 PairEvidence, Relation)
from methadata.contracts import ids
from methadata.contracts.models import ArtifactRole, ValueType

from .jaccard import jaccard, weighted_jaccard

EXTRACTOR_VERSION = "assets-1"
FAMILY = "asset"
KIND_IMAGE = "image_sha256"            # zbiór hashy grafik (kreacja + zasoby graficzne)
KIND_SCREENSHOT = "screenshot_sha256"  # główny zrzut: osobna rola, nie wchodzi do zbioru grafik


def _is_image(a) -> bool:
    if a.role is ArtifactRole.creative_image:
        return True
    return a.role is ArtifactRole.resource and (a.media_type or "").lower().startswith("image/")


class AssetHashExtractor:
    spec = AdapterSpec(id="assets-hash-extractor", kind=AdapterKind.extractor, version=EXTRACTOR_VERSION,
                       description="Dokładne SHA-256 grafik (creative_image, zasoby image/*); zrzut strony osobno")

    def extract(self, observation, *, reader=None, optional=None):
        feats: list[Feature] = []
        seen: set[tuple[str, str]] = set()
        n_img = 0
        for a in observation.artifacts:
            if _is_image(a):
                kind = KIND_IMAGE
            elif a.role is ArtifactRole.landing_screenshot:
                kind = KIND_SCREENSHOT
            else:
                continue
            n_img += kind == KIND_IMAGE
            if (kind, a.artifact_id) in seen:
                continue
            seen.add((kind, a.artifact_id))
            feats.append(Feature(
                feature_id=ids.feature_id(observation.observation_id, FAMILY, kind, EXTRACTOR_VERSION,
                                          [a.sha256, a.artifact_id]),
                observation_id=observation.observation_id, family=FAMILY, kind=kind,
                value_type=ValueType.string, value=a.sha256, extractor_version=EXTRACTOR_VERSION,
                evidence_refs=[ArtifactRef(artifact_id=a.artifact_id)]))
        state = ExtractionState.ok
        detail = None if n_img else "brak grafik; pusty zbiór to brak podstaw, nie zerowe podobieństwo"
        return feats, [ExtractionStatus(observation_id=observation.observation_id,
                                        extractor_version=EXTRACTOR_VERSION, state=state, detail=detail)]


def _image_set(features: Iterable[Feature]) -> dict[str, list[ArtifactRef]]:
    out: dict[str, list[ArtifactRef]] = {}
    for f in features:
        if f.family == FAMILY and f.kind == KIND_IMAGE:
            out.setdefault(f.value, []).extend(f.evidence_refs)
    return out


def corpus_frequencies(feature_map: Mapping[str, Sequence[Feature]]) -> dict[str, int]:
    """Liczba obserwacji korpusu zawierających dany hash grafiki (observation_id -> cechy)."""
    freq: dict[str, int] = {}
    for feats in feature_map.values():
        for h in _image_set(feats):
            freq[h] = freq.get(h, 0) + 1
    return freq


class _Base:
    weighted = False
    method_id = ""

    def __init__(self, frequencies: Optional[Mapping[str, int]] = None):
        """frequencies: hash -> liczba materiałów korpusu (tylko wariant ważony; bez nich wagi = 1/1,
        czyli wynik równy zwykłemu Jaccardowi — nie jest to ważenie korpusowe)."""
        self.frequencies = dict(frequencies or {})

    def compare(self, a, b, features, *, run_id, optional=None) -> Sequence[PairEvidence]:
        (ca, fa), (cb, fb) = sorted([(a.canonical_id, features.get(a.observation_id, [])),
                                     (b.canonical_id, features.get(b.observation_id, []))], key=lambda t: t[0])
        sa, sb = _image_set(fa), _image_set(fb)
        pair = (ca, cb)

        def none(why: str):
            return [PairEvidence(run_id=run_id, pair=pair, method_id=self.method_id,
                                 relation=Relation.shared_template, score=None, explanation=why)]

        if not sa and not sb:
            return none("brak podstaw: oba zbiory grafik są puste")
        if not sa or not sb:
            return none("brak podstaw: jedna ze stron nie ma grafik (brak dowodów po obu stronach)")
        shared = sorted(set(sa) & set(sb))
        score = (weighted_jaccard(sa, sb, self.frequencies) if self.weighted else jaccard(sa, sb))
        if shared:
            refs_a = [r for h in shared for r in sa[h]]
            refs_b = [r for h in shared for r in sb[h]]
        else:
            refs_a = [r for rs in sa.values() for r in rs]
            refs_b = [r for rs in sb.values() for r in rs]
        if not refs_a or not refs_b:
            return none("brak podstaw: cechy bez odwołań do artefaktów")
        refs = {ca: refs_a, cb: refs_b}
        inter, union = len(shared), len(set(sa) | set(sb))
        files = ", ".join(f"{h[:12]} (korpus: {self.frequencies.get(h, 1) if self.weighted else '-'})"
                          if self.weighted else h[:12] for h in shared) or "brak"
        kind = "ważony odwrotnością częstości" if self.weighted else "dokładny"
        expl = (f"Jaccard {kind} zbiorów hashy grafik: {inter}/{union} wspólnych, wynik {score:.4f}. "
                f"Wspólne pliki: {files}.")
        return [PairEvidence(run_id=run_id, pair=pair, method_id=self.method_id,
                             relation=Relation.shared_template, score=score, evidence_refs=refs, explanation=expl)]


class AssetJaccardComparator(_Base):
    method_id = "assets-jaccard-v1"
    spec = AdapterSpec(id=method_id, kind=AdapterKind.comparator, version="1",
                       description="Dokładny Jaccard zbiorów SHA-256 grafik")


class WeightedAssetJaccardComparator(_Base):
    weighted = True
    method_id = "assets-weighted-jaccard-v1"
    spec = AdapterSpec(id=method_id, kind=AdapterKind.comparator, version="1",
                       description="Jaccard zbiorów SHA-256 grafik ważony 1/częstość w korpusie")


def get_adapters() -> list[Any]:
    """Komparator ważony bez częstości działa jak zwykły; runner F03 powinien go zbudować z corpus_frequencies()."""
    return [AssetHashExtractor(), AssetJaccardComparator(), WeightedAssetJaccardComparator()]
