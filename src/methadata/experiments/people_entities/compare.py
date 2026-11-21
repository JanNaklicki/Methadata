"""Komparatory encji. Dwa OSOBNE typy dowodu (osobne method_id): wzmianka tekstowa i osoba widoczna na obrazie.

Wynik to zawsze Relation.shared_narrative: zbieżność nazwiska (marki, produktu) jest sygnałem tematycznym i NIE
rozstrzyga o kampanii; o grupie decyduje dopiero fuzja z innymi dowodami (zasoby, szablon, sieć)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional, Sequence

from methadata.contracts import AdapterKind, AdapterSpec, ArtifactRef, PairEvidence, Relation

from .extractor import FAMILY, KIND_MENTION, KIND_VISIBLE, ROLE_MENTION
from .lexicon import Lexicon, default_lexicon

# waga roli/pola wzmianki w profilu dokumentu (wartość największa z wystąpień encji)
ROLE_WEIGHTS = {"title": 1.0, "caption": 0.8, "ocr": 0.6, "body": 0.5, "cta": 0.3, "comments": 0.2}
NOT_DECISIVE = ("Zbieżność nazw własnych jest sygnałem tematycznym i sama nie rozstrzyga o wspólnej kampanii; "
                "brak wspólnej osoby też jej nie wyklucza.")
MAX_REFS = 6


@dataclass
class Mention:
    entity_id: str
    entity_type: str
    canonical: str
    surface: str
    label: str            # title/body/cta/comments/caption/ocr
    source: str           # html | ocr
    refs: list = field(default_factory=list)


def mentions(features: Iterable[Any], lexicon: Optional[Lexicon] = None) -> list[Mention]:
    """Wzmianki tekstowe z cech E08 (HTML) oraz, opcjonalnie, z cech OCR E10 (`ocr/ocr_boxes`) – gdy są dostępne."""
    lex = lexicon or default_lexicon()
    out: list[Mention] = []
    for f in features:
        v = f.value
        if f.family == FAMILY and f.kind == KIND_MENTION:
            out.append(Mention(v["entity_id"], v["entity_type"], v["canonical"], v["surface"], v["field"], "html",
                               list(f.evidence_refs)))
        elif f.family == "ocr" and f.kind == "ocr_boxes" and isinstance(v, dict):
            lines = v.get("lines", [])
            ev = list(f.evidence_refs)
            for i, ln in enumerate(lines):
                r = [ev[i]] if len(ev) == len(lines) else (ev[:1] if ev else [])
                for h in lex.find(ln.get("text", "")):
                    out.append(Mention(h.entity_id, h.entity_type, h.canonical, h.surface, "ocr", "ocr", r))
    return out


def visible_persons(features: Iterable[Any]) -> list[Any]:
    return [f for f in features if f.family == FAMILY and f.kind == KIND_VISIBLE]


def profile(ms: Sequence[Mention], types: Optional[Sequence[str]] = None) -> dict[str, dict]:
    """entity_id -> {weight, type, canonical, labels, refs, n}."""
    p: dict[str, dict] = {}
    for m in ms:
        if types and m.entity_type not in types:
            continue
        e = p.setdefault(m.entity_id, {"weight": 0.0, "type": m.entity_type, "canonical": m.canonical,
                                       "labels": set(), "refs": [], "n": 0})
        e["weight"] = max(e["weight"], ROLE_WEIGHTS.get(m.label, 0.5))
        e["labels"].add(m.label)
        e["refs"] += m.refs
        e["n"] += 1
    return p


def weighted_jaccard(pa: Mapping[str, dict], pb: Mapping[str, dict]) -> float:
    keys = set(pa) | set(pb)
    num = sum(min(pa.get(k, {}).get("weight", 0.0), pb.get(k, {}).get("weight", 0.0)) for k in keys)
    den = sum(max(pa.get(k, {}).get("weight", 0.0), pb.get(k, {}).get("weight", 0.0)) for k in keys)
    return num / den if den else 0.0


def _dedupe(refs: Sequence[ArtifactRef], cap: int = MAX_REFS) -> list[ArtifactRef]:
    seen, out = set(), []
    for r in refs:
        k = r.model_dump_json()
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out[:cap]


def _order(a, b):
    return sorted([(a.canonical_id, a), (b.canonical_id, b)], key=lambda t: t[0])


class PeopleTextComparator:
    """Wzmianki osób/marek/produktów w tekście (HTML, opcjonalnie OCR). Score = ważony Jaccard zbiorów encji
    (waga: tytuł > podpis > OCR > korpus > CTA > komentarze). Brak wzmianek po którejś stronie = brak podstaw (None),
    bo słownik jest mały; brak wspólnych encji przy obu niepustych = 0.0."""

    def __init__(self, *, method_id: str = "people-text-mention-v1", types: Optional[Sequence[str]] = None,
                 lexicon: Optional[Lexicon] = None):
        self.method_id = method_id
        self.types = tuple(types) if types else None
        self.lexicon = lexicon
        self.spec = AdapterSpec(id=method_id, kind=AdapterKind.comparator, version="1",
                                description="Wzmianki tekstowe encji (osoby, marki, produkty); osobny dowód od osoby widocznej")

    def compare(self, a, b, features, *, run_id, optional=None) -> Sequence[PairEvidence]:
        (ca, oa), (cb, ob) = _order(a, b)
        pair = (ca, cb)
        pa = profile(mentions(features.get(oa.observation_id, []), self.lexicon), self.types)
        pb = profile(mentions(features.get(ob.observation_id, []), self.lexicon), self.types)

        def none(why):
            return [PairEvidence(run_id=run_id, pair=pair, method_id=self.method_id, relation=Relation.shared_narrative,
                                 score=None, explanation=why)]
        if not pa or not pb:
            return none("brak podstaw: " + ("obie strony nie mają" if not pa and not pb else
                        f"strona {ca if not pa else cb} nie ma") +
                        " wzmianek encji ze słownika (słownik jest mały, brak trafień nie oznacza braku nazw)")
        shared = sorted(set(pa) & set(pb))
        score = weighted_jaccard(pa, pb)
        refs = {ca: [], cb: []}
        for k in shared or ():
            refs[ca] += pa[k]["refs"]
            refs[cb] += pb[k]["refs"]
        if not shared:  # dowodem rozbieżności są własne wzmianki stron
            for k in sorted(pa):
                refs[ca] += pa[k]["refs"][:2]
            for k in sorted(pb):
                refs[cb] += pb[k]["refs"][:2]
        refs = {k: _dedupe(v) for k, v in refs.items()}
        if not refs[ca] or not refs[cb]:
            return none("brak podstaw: wzmianki bez odwołań do artefaktów")

        def desc(p, k):
            return f"{p[k]['canonical']} [{p[k]['type']}; {','.join(sorted(p[k]['labels']))}; x{p[k]['n']}]"
        txt = f"Wzmianki tekstowe, ważony Jaccard {score:.3f}. "
        if shared:
            txt += "Wspólne: " + "; ".join(f"{desc(pa, k)} | {desc(pb, k)}" for k in shared) + ". "
        else:
            txt += "Brak wspólnych encji. "
        only_a = sorted(set(pa) - set(pb))
        only_b = sorted(set(pb) - set(pa))
        if only_a:
            txt += f"Tylko {ca}: " + ", ".join(pa[k]["canonical"] for k in only_a) + ". "
        if only_b:
            txt += f"Tylko {cb}: " + ", ".join(pb[k]["canonical"] for k in only_b) + ". "
        txt += NOT_DECISIVE
        return [PairEvidence(run_id=run_id, pair=pair, method_id=self.method_id, relation=Relation.shared_narrative,
                             score=min(1.0, max(0.0, score)), evidence_refs=refs, explanation=txt)]


class PeopleVisibleComparator:
    """Osoba widoczna na obrazie (adnotacja z obrazu referencyjnego, dopasowanie po SHA-256). Osobny typ dowodu od
    wzmianki tekstowej: osoba wspomniana jedynie w treści NIE jest tu osobą widoczną (brak adnotacji = brak podstaw)."""

    def __init__(self, *, method_id: str = "people-visible-person-v1"):
        self.method_id = method_id
        self.spec = AdapterSpec(id=method_id, kind=AdapterKind.comparator, version="1",
                                description="Osoba widoczna na obrazie (ręcznie opisane obrazy referencyjne, SHA-256)")

    def compare(self, a, b, features, *, run_id, optional=None) -> Sequence[PairEvidence]:
        (ca, oa), (cb, ob) = _order(a, b)
        pair = (ca, cb)
        va, vb = visible_persons(features.get(oa.observation_id, [])), visible_persons(features.get(ob.observation_id, []))

        def none(why):
            return [PairEvidence(run_id=run_id, pair=pair, method_id=self.method_id, relation=Relation.shared_narrative,
                                 score=None, explanation=why)]
        if not va or not vb:
            return none("brak podstaw: " + ("żadna ze stron nie ma" if not va and not vb else
                        f"strona {ca if not va else cb} nie ma") +
                        " adnotacji osoby widocznej (wzmianka tylko w treści to inny typ dowodu)")
        ea = {f.value["entity_id"]: f for f in va}
        eb = {f.value["entity_id"]: f for f in vb}
        shared = sorted(set(ea) & set(eb))
        score = len(shared) / len(set(ea) | set(eb))
        refs = {ca: _dedupe([r for k in (shared or sorted(ea)) for r in ea[k].evidence_refs]),
                cb: _dedupe([r for k in (shared or sorted(eb)) for r in eb[k].evidence_refs])}

        def lab(f):
            v = f.value
            return f"{v['canonical']} (źródło etykiety: {v['label_source']}; zweryfikowana: {'tak' if v['verified'] else 'nie'})"
        txt = f"Osoba widoczna, Jaccard {score:.3f}. "
        txt += ("Wspólne: " + "; ".join(lab(ea[k]) for k in shared) + ". ") if shared else "Brak wspólnej osoby widocznej. "
        txt += NOT_DECISIVE + " Dopasowanie po SHA-256 obrazu referencyjnego, nie rozpoznawanie twarzy."
        return [PairEvidence(run_id=run_id, pair=pair, method_id=self.method_id, relation=Relation.shared_narrative,
                             score=score, evidence_refs=refs, explanation=txt)]
