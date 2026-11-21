"""E03: podobieństwo struktury DOM. Tekst i zmienne identyfikatory (id, class, atrybuty) zastąpione znacznikami;
porównywane: zbiór ścieżek tagów, kolejność sekcji (landmarków) oraz sekwencja tagów (odległość edycyjna)."""
from __future__ import annotations

from typing import Any, Optional, Sequence

from methadata.contracts import (AdapterKind, AdapterSpec, ArtifactRef, ExtractionState, ExtractionStatus, Feature,
                                 PairEvidence, Relation)
from methadata.contracts import ids
from methadata.contracts.models import ArtifactRole, ByteRange, TextSpan, ValueType
from methadata.extractors.html.extractor import _decode
from methadata.extractors.html.mhtml import looks_like_mhtml, parse_mhtml
from methadata.extractors.html.parse import norm_ws, parse_html

from . import seq

EXTRACTOR_VERSION = "dom-struct-1"
FAMILY = "dom_struct"
KIND = "dom_sequence"
_ROLES = (ArtifactRole.landing_dom, ArtifactRole.landing_html)  # zapis renderowany ma pierwszeństwo
MAX_REFS = 10


class DomStructureExtractor:
    """Cecha `dom_struct/dom_sequence` (json): tokeny [głębokość (od 0 = korzeń), tag, start, end] w kolejności dokumentu;
    znacznik tekstu '#t' zastępuje treść. Bez <head>, script, style. Zakresy to TextSpan (lub ByteRange części MHTML)."""
    spec = AdapterSpec(id="dom-struct-extractor", kind=AdapterKind.extractor, version=EXTRACTOR_VERSION,
                       description="Znormalizowana sekwencja tagów DOM (bez tekstu i identyfikatorów) z lokalizatorami")

    def extract(self, observation, *, reader, optional=None):
        oid = observation.observation_id

        def st(state, detail, art=None):
            return ExtractionStatus(observation_id=oid, artifact_id=art, extractor_version=EXTRACTOR_VERSION,
                                    state=state, detail=detail)

        arts = [a for r in _ROLES for a in observation.artifacts if a.role is r]
        if not arts:
            return [], [st(ExtractionState.missing_input, "brak artefaktu landing_dom/landing_html")]
        art = arts[0]
        try:
            raw = reader.open_bytes(ArtifactRef(artifact_id=art.artifact_id))
        except Exception as e:
            return [], [st(ExtractionState.missing_input, f"nie można odczytać bajtów: {e}", art.artifact_id)]
        try:
            byte_loc = None
            if looks_like_mhtml(raw, art.media_type, art.origin or art.local_ref):
                mh = parse_mhtml(raw)
                main = mh.main_part()
                if main is None:
                    return [], [st(ExtractionState.missing_input, "MHTML bez części text/html", art.artifact_id)]
                text, _ = _decode(main.data, main.charset)
                byte_loc = ByteRange(start=main.byte_start, end=main.byte_end)
            else:
                text, _ = _decode(raw, None)
            doc = parse_html(text)
            tokens: list[list] = []
            skip_depth: Optional[int] = None
            for n in doc.nodes:
                if skip_depth is not None:
                    if n.depth > skip_depth:
                        continue
                    skip_depth = None
                if n.tag in seq.SKIP_TAGS:
                    skip_depth = n.depth
                    continue
                tokens.append([n.depth - 1, n.tag, n.open_start, max(n.end, n.open_start + 1)])
                if any(norm_ws("".join(r.parts)) for r in n.runs):
                    tokens.append([n.depth, seq.TEXT, n.open_start, max(n.end, n.open_start + 1)])
        except Exception as e:
            return [], [st(ExtractionState.failed, f"{type(e).__name__}: {e}", art.artifact_id)]
        ref = ArtifactRef(artifact_id=art.artifact_id, locator=byte_loc)
        value = {"tokens": tokens, "capture": "mhtml" if byte_loc else "html", "chars": len(text)}
        f = Feature(feature_id=ids.feature_id(oid, FAMILY, KIND, EXTRACTOR_VERSION, [art.artifact_id, value]),
                    observation_id=oid, family=FAMILY, kind=KIND, value_type=ValueType.json, value=value,
                    extractor_version=EXTRACTOR_VERSION, evidence_refs=[ref])
        return [f], [st(ExtractionState.ok, f"tokenów={len(tokens)}" + ("" if tokens else "; pusta struktura"), art.artifact_id)]


def _load(features: Sequence[Feature]):
    for f in features:
        if f.family == FAMILY and f.kind == KIND and f.value.get("tokens"):
            return f
    return None


class _Base:
    method_id = ""
    variant = ""

    def __init__(self, drop_tags: Sequence[str] = ()):
        """drop_tags: tagi, których poddrzewa pomijamy (np. ('h1',) = wynik po usunięciu nagłówka)."""
        self.drop_tags = tuple(drop_tags)

    # --- widoki struktury po usunięciu tagów
    def view(self, feat: Feature):
        toks = feat.value["tokens"]
        idx = seq.drop_subtrees(toks, self.drop_tags)
        return toks, idx

    def sequence(self, toks, idx):
        return [f"{toks[i][0]}:{toks[i][1]}" for i in idx]

    def _score(self, ta, ia, tb, ib):  # -> (score, opis, bloki (indeksy w ia/ib))
        raise NotImplementedError

    def compare(self, a, b, features, *, run_id, optional=None) -> Sequence[PairEvidence]:
        (ca, oa, fa), (cb, ob, fb) = sorted(
            [(a.canonical_id, a.observation_id, _load(features.get(a.observation_id, []))),
             (b.canonical_id, b.observation_id, _load(features.get(b.observation_id, [])))], key=lambda t: t[0])
        pair = (ca, cb)

        def none(why):
            return [PairEvidence(run_id=run_id, pair=pair, method_id=self.method_id, relation=Relation.shared_template,
                                 score=None, explanation=why)]

        if fa is None or fb is None:
            return none("brak podstaw: brak cechy struktury DOM po jednej ze stron lub po obu")
        (ta, ia), (tb, ib) = self.view(fa), self.view(fb)
        if not ia or not ib:
            return none("brak podstaw: pusta struktura po normalizacji")
        score, desc, blocks = self._score(ta, ia, tb, ib)
        if score is None:
            return none("brak podstaw: " + desc)
        refs = {ca: [], cb: []}
        for (ia_, ib_, n) in sorted(blocks, key=lambda x: -x[2])[:MAX_REFS]:
            for cid, f, t, idx, s in ((ca, fa, ta, ia, ia_), (cb, fb, tb, ib, ib_)):
                refs[cid].append(self._ref(f, t, idx, s, n))
        for cid, f in ((ca, fa), (cb, fb)):
            if not refs[cid]:  # brak wspólnych bloków: odwołanie do całej struktury
                refs[cid] = [f.evidence_refs[0]]
        drop = f"; usunięto poddrzewa: {','.join(self.drop_tags)}" if self.drop_tags else ""
        expl = f"{desc}, wynik {score:.4f} ({self.variant}){drop}."
        return [PairEvidence(run_id=run_id, pair=pair, method_id=self.method_id, relation=Relation.shared_template,
                             score=score, evidence_refs=refs, explanation=expl)]

    @staticmethod
    def _ref(feat, toks, idx, start, n):
        base = feat.evidence_refs[0]
        if isinstance(base.locator, ByteRange):
            return base
        s = toks[idx[start]][2]
        e = max(toks[idx[start + n - 1]][3], s + 1)
        return ArtifactRef(artifact_id=base.artifact_id, locator=TextSpan(start=s, end=e))


class PathSetComparator(_Base):
    method_id = "dom-paths-jaccard-v1"
    variant = "Jaccard zbiorów ścieżek tagów"
    spec = AdapterSpec(id=method_id, kind=AdapterKind.comparator, version="1",
                       description="Jaccard zbiorów ścieżek tagów DOM (tekst i identyfikatory zastąpione znacznikami)")

    def _score(self, ta, ia, tb, ib):
        pa, pb = seq.tag_paths(ta, ia), seq.tag_paths(tb, ib)
        sa, sb = set(pa), set(pb)
        sc = seq.jaccard(sa, sb)
        shared_paths = sa & sb
        # bloki: kolejne tokeny, których ścieżka należy do wspólnego zbioru
        ma = [p in shared_paths for p in pa]
        mb = [p in shared_paths for p in pb]
        blocks = seq.common_blocks([p if m else f"\0a{i}" for i, (p, m) in enumerate(zip(pa, ma))],
                                   [p if m else f"\0b{i}" for i, (p, m) in enumerate(zip(pb, mb))])
        return sc, f"Jaccard ścieżek tagów: {len(shared_paths)}/{len(sa | sb)} wspólnych ścieżek", blocks


class SequenceEditComparator(_Base):
    method_id = "dom-sequence-edit-v1"
    variant = "1 - odległość edycyjna / max długość"
    spec = AdapterSpec(id=method_id, kind=AdapterKind.comparator, version="1",
                       description="Odległość edycyjna sekwencji (głębokość:tag) w kolejności dokumentu")

    def _score(self, ta, ia, tb, ib):
        sa, sb = self.sequence(ta, ia), self.sequence(tb, ib)
        d = seq.levenshtein(sa, sb)
        blocks = seq.common_blocks(sa, sb)
        common = sum(n for _, _, n in blocks)
        return (1.0 - d / max(len(sa), len(sb)),
                f"odległość edycyjna {d} przy długościach {len(sa)}/{len(sb)}; wspólna sekwencja {common} tokenów "
                f"w {len(blocks)} blokach", blocks)


class SectionOrderComparator(_Base):
    method_id = "dom-sections-v1"
    variant = "1 - odległość edycyjna kolejności sekcji / max długość"
    spec = AdapterSpec(id=method_id, kind=AdapterKind.comparator, version="1",
                       description="Kolejność sekcji strukturalnych (header, nav, main, article, section, h1-h3, ...)")

    def _score(self, ta, ia, tb, ib):
        la = [i for i in ia if ta[i][1] in seq.LANDMARKS]
        lb = [i for i in ib if tb[i][1] in seq.LANDMARKS]
        sa, sb = [ta[i][1] for i in la], [tb[i][1] for i in lb]
        if not sa or not sb:
            return None, "jedna ze stron nie ma sekcji strukturalnych", []
        d = seq.levenshtein(sa, sb)
        blocks = seq.common_blocks(sa, sb)
        # bloki w indeksach sekwencji sekcji -> indeksy tokenów
        mapped = []
        inv_a = {i: k for k, i in enumerate(ia)}
        pos_b = {i: k for k, i in enumerate(ib)}
        for (x, y, n) in blocks:
            mapped.append((inv_a[la[x]], pos_b[lb[y]], 1))
        return (1.0 - d / max(len(sa), len(sb)),
                f"kolejność sekcji: odległość edycyjna {d}, długości {len(sa)}/{len(sb)}, "
                f"wspólnych {sum(n for _, _, n in blocks)}", mapped)


def get_adapters() -> list[Any]:
    return [DomStructureExtractor(), PathSetComparator(), SequenceEditComparator(), SectionOrderComparator()]
