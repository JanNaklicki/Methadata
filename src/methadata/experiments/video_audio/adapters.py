"""E16: podobieństwo wideo i audio.

Ekstraktor `video-audio-extractor` zapisuje (family `video_audio`):
 - `video`       json {artifact_id, width, height, fps, duration_ms, interval_ms, frames[{time_ms,phash}], shots[...],
                       audio_track: obecna|brak|nieznana}
 - `audio`       json {artifact_id, duration_ms, source, hop_ms, frame_ms, bits, valid}   (odcisk spektralny)
 - `transcript`  json {artifact_id, backend, segments[{start_ms,end_ms,text}]}             (tylko z backendem)
 - `voice`       json {artifact_id, mean, std, voiced_s, backend}                          (sygnał pomocniczy)

Komparatory zwracają PairEvidence; każdy dowód ma przedział czasu po obu stronach zakodowany jako dwie
ArtifactRef z ImageRect.frame_ms (początek i koniec; dla audio prostokąt 1x1 jest tylko nośnikiem czasu), a
`explanation` zawiera przedziały w ms oraz cytat (transkrypcja) lub numery klatek. Głos jest osobnym, pomocniczym
sygnałem (`voice-aux-v1`, relation campaign_candidate) i nigdy nie wchodzi do wyniku innych metod."""
from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from methadata.contracts import (AdapterKind, AdapterSpec, ArtifactRef, ExtractionState, ExtractionStatus, Feature,
                                 PairEvidence, Relation, ids)
from methadata.contracts.models import ArtifactRole, ImageRect, ValueType
from methadata.extractors.media import probe as MP

from . import audio as AU
from . import match as M
from . import video as V
from .config import DEFAULTS, load_config

EXTRACTOR_VERSION = "video-audio-1"
FAMILY = "video_audio"
K_VIDEO, K_AUDIO, K_TRANSCRIPT, K_VOICE = "video", "audio", "transcript", "voice"


def _is_av(a) -> bool:
    if a.role in (ArtifactRole.video, ArtifactRole.audio):
        return True
    return a.role is ArtifactRole.resource and (a.media_type or "").lower().split("/")[0] in ("video", "audio")


class VideoAudioExtractor:
    spec = AdapterSpec(id="video-audio-extractor", kind=AdapterKind.extractor, version=EXTRACTOR_VERSION,
                       description="Klatki (pHash) i ujęcia wideo, odcisk audio, profil głosu, opcjonalna transkrypcja",
                       extras=["perceptual"])

    def __init__(self, config: Optional[Mapping[str, Any]] = None, transcriber: Optional[AU.Transcriber] = None):
        self.cfg = {**DEFAULTS, **(config or {})}
        self.transcriber = transcriber

    def _feat(self, oid, art, kind, value):
        return Feature(feature_id=ids.feature_id(oid, FAMILY, kind, EXTRACTOR_VERSION, [art.artifact_id, kind]),
                       observation_id=oid, family=FAMILY, kind=kind, value_type=ValueType.json,
                       value={"artifact_id": art.artifact_id, **value}, extractor_version=EXTRACTOR_VERSION,
                       evidence_refs=[ArtifactRef(artifact_id=art.artifact_id)])

    def extract(self, observation, *, reader, optional: Optional[Mapping[str, Any]] = None):
        oid = observation.observation_id
        feats: list[Feature] = []
        sts: list[ExtractionStatus] = []

        def status(art, state, detail=None):
            sts.append(ExtractionStatus(observation_id=oid, artifact_id=art.artifact_id if art else None,
                                        extractor_version=EXTRACTOR_VERSION, state=state, detail=detail))
        media = [a for a in observation.artifacts if _is_av(a)]
        if not media:
            status(None, ExtractionState.ok, "brak wideo i audio; pusty wynik to brak podstaw")
        for art in media:
            try:
                data = reader.open_bytes(ArtifactRef(artifact_id=art.artifact_id))
            except (FileNotFoundError, KeyError, OSError) as e:
                status(art, ExtractionState.missing_input, f"brak pliku {art.local_ref}: {type(e).__name__}")
                continue
            if not data:
                status(art, ExtractionState.missing_input, f"pusty plik {art.local_ref}")
                continue
            if len(data) > self.cfg["max_bytes"]:
                status(art, ExtractionState.unsupported, f"plik większy niż {self.cfg['max_bytes']} B; pominięty")
                continue
            p = MP.probe_bytes(data)
            if p.kind not in ("video", "audio"):
                status(art, ExtractionState.unsupported, "nie jest wideo ani audio")
                continue
            notes: list[str] = []
            state = ExtractionState.ok
            audio_source: Optional[bytes] = None
            if p.kind == "video":
                try:
                    v = V.read_frames(data, p.format, self.cfg)
                except V.VideoUnsupported as e:
                    status(art, ExtractionState.unsupported, str(e))
                    continue
                except Exception as e:  # noqa: BLE001 - uszkodzony plik to brak cechy
                    status(art, ExtractionState.failed, f"dekodowanie wideo: {type(e).__name__}")
                    continue
                kinds = {t.get("type") for t in p.tracks}
                track = "obecna" if "audio" in kinds else ("brak" if p.tracks else "nieznana")
                feats.append(self._feat(oid, art, K_VIDEO, {
                    **v, "interval_ms": int(self.cfg["frame_interval_ms"]), "audio_track": track,
                    "shots": V.shots(v["frames"], v["duration_ms"], self.cfg)}))
                if not v["frames"]:
                    notes.append("nie zdekodowano żadnej klatki")
                    state = ExtractionState.failed
                if track == "brak":
                    notes.append("brak ścieżki audio (porównanie audio nie dotyczy tego materiału)")
                else:
                    audio_source = AU.to_wav(data, p.format, self.cfg["audio_max_seconds"])
                    if audio_source is None:
                        notes.append(f"ścieżka audio ({track}) niedekodowalna bez ffmpeg: audio unsupported")
            else:
                audio_source = AU.to_wav(data, p.format, self.cfg["audio_max_seconds"])
                if audio_source is None:
                    status(art, ExtractionState.unsupported,
                           f"audio {p.format} wymaga ffmpeg (brak); obsługiwany jest WAV")
                    continue
            if audio_source is not None:
                notes.extend(self._audio(oid, art, audio_source, feats))
            status(art, state, "; ".join(notes) or None)
        return feats, sts

    def _audio(self, oid, art, wav: bytes, feats) -> list[str]:
        notes = []
        try:
            x, sr = AU.decode_wav(wav, self.cfg["audio_max_seconds"])
            fp = AU.fingerprint(x, sr, self.cfg)
        except AU.AudioUnsupported as e:
            return [f"audio unsupported: {e}"]
        dur = int(len(x) * 1000 / sr)
        feats.append(self._feat(oid, art, K_AUDIO, {"duration_ms": dur, "source": "wav", **fp}))
        if not any(c == "1" for c in fp["valid"]):
            notes.append("audio ciche: odcisk bez ważnych ramek")
        vp = AU.voice_profile(x, sr, self.cfg)
        if vp is not None:
            feats.append(self._feat(oid, art, K_VOICE, vp))
        else:
            notes.append("za mało mowy na profil głosu")
        if self.transcriber is not None:
            try:
                segs = [dict(s) for s in self.transcriber(wav)]
                feats.append(self._feat(oid, art, K_TRANSCRIPT, {"backend": "injected", "segments": segs}))
            except Exception as e:  # noqa: BLE001 - backend zewnętrzny
                notes.append(f"transkrypcja nieudana: {type(e).__name__}")
        else:
            notes.append("transkrypcja: brak backendu (unsupported)")
        return notes


def _items(features: Sequence[Feature], kind: str, cfg) -> list[Mapping[str, Any]]:
    out = [f.value for f in features if f.family == FAMILY and f.kind == kind]
    out.sort(key=lambda v: v["artifact_id"])
    return out[: int(cfg["max_items"])]


def _span(art_id: str, w: int, h: int, t0: int, t1: int) -> list[ArtifactRef]:
    t0, t1 = int(t0), max(int(t1), int(t0))
    return [ArtifactRef(artifact_id=art_id, locator=ImageRect(x=0, y=0, width=max(1, w), height=max(1, h), frame_ms=t0)),
            ArtifactRef(artifact_id=art_id, locator=ImageRect(x=0, y=0, width=max(1, w), height=max(1, h), frame_ms=t1))]


class _Base:
    relation = Relation.shared_template
    kind = ""
    method_id = ""
    no_data = "brak materiału"

    def __init__(self, config: Optional[Mapping[str, Any]] = None):
        self.cfg = {**DEFAULTS, **(config or {})}

    def _none(self, pair, run_id, why):
        return [PairEvidence(run_id=run_id, pair=pair, method_id=self.method_id, relation=self.relation,
                             score=None, explanation=why)]

    def compare(self, a, b, features, *, run_id, optional=None) -> Sequence[PairEvidence]:
        (ca, oa), (cb, ob) = sorted([(a.canonical_id, a), (b.canonical_id, b)], key=lambda t: t[0])
        pair = (ca, cb)
        ia = _items(features.get(oa.observation_id, []), self.kind, self.cfg)
        ib = _items(features.get(ob.observation_id, []), self.kind, self.cfg)
        if not ia or not ib:
            return self._none(pair, run_id, f"brak podstaw: {self.no_data}")
        try:
            res = self._best(ia, ib)
        except ImportError as e:
            return self._none(pair, run_id, f"brak podstaw: brak biblioteki ({e.name}); zainstaluj extra perceptual")
        if res is None:
            return self._none(pair, run_id, "brak podstaw: materiał zbyt krótki lub bez ważnych próbek")
        score, refs_a, refs_b, expl = res
        return [PairEvidence(run_id=run_id, pair=pair, method_id=self.method_id, relation=self.relation,
                             score=min(1.0, max(0.0, score)), evidence_refs={ca: refs_a, cb: refs_b},
                             explanation=expl)]


def _fmt(t: tuple[int, int]) -> str:
    return f"{t[0]}-{t[1]} ms"


class VideoFramesComparator(_Base):
    kind = K_VIDEO
    method_id = "video-frames-v1"
    no_data = "po jednej ze stron brak wideo z zdekodowanymi klatkami"
    spec = AdapterSpec(id=method_id, kind=AdapterKind.comparator, version="1",
                       description="Klatki kluczowe (pHash) i sekwencje ujęć; fragment krótszego w dłuższym",
                       extras=["perceptual"])

    def _best(self, ia, ib):
        cfg = self.cfg
        best = None
        usable = False
        for x in ia:
            for y in ib:
                if min(len(x["frames"]), len(y["frames"])) < cfg["min_run_frames"]:
                    continue
                usable = True
                shorter = max(1, min(x["duration_ms"], y["duration_ms"]))
                fr = M.frame_run(x["frames"], y["frames"], cfg)
                if fr is not None and fr["hits"] < cfg["min_run_frames"]:
                    fr = None
                sh = M.shot_match(x["shots"], y["shots"], cfg)
                s_fr = 0.0 if fr is None else min(1.0, fr["hits"] * x["interval_ms"] / shorter)
                s_sh = 0.0 if sh is None else min(1.0, min(sh["duration_a_ms"], sh["duration_b_ms"]) / shorter)
                sc = max(s_fr, s_sh)
                if best is None or sc > best[0]:
                    best = (sc, x, y, fr, sh, s_fr, s_sh)
        if not usable:
            return None
        sc, x, y, fr, sh, s_fr, s_sh = best
        if sc == 0.0:
            return (0.0, [ArtifactRef(artifact_id=x["artifact_id"])], [ArtifactRef(artifact_id=y["artifact_id"])],
                    f"Klatki+ujęcia: brak wspólnego fragmentu (>= {self.cfg['min_run_frames']} klatek lub "
                    f">= {self.cfg['min_shot_matches']} ujęć w kolejności) przy progu pHash {self.cfg['hash_threshold']}.")
        # dowód: przedział z silniejszej metody (przy remisie klatki)
        use = fr if (fr is not None and s_fr >= s_sh) else sh
        ta, tb = use["a"], use["b"]
        parts = []
        if fr is not None:
            parts.append(f"klatki: {fr['hits']} zgodnych (próbkowanie {x['interval_ms']} ms, średni Hamming "
                         f"{fr['mean_hamming']}, A klatki od #{fr['index_a']}, B od #{fr['index_b']}), wynik {s_fr:.3f}")
        if sh is not None:
            parts.append(f"ujęcia: {sh['shots']} wspólnych w tej samej kolejności, wynik {s_sh:.3f}")
        expl = (f"Wspólny fragment wideo: A {_fmt(ta)} odpowiada B {_fmt(tb)}; " + "; ".join(parts) +
                f". Wynik = max(...) = {sc:.3f}, pokrycie względem krótszego materiału ({min(x['duration_ms'], y['duration_ms'])} ms).")
        return (sc, _span(x["artifact_id"], x["width"], x["height"], *ta),
                _span(y["artifact_id"], y["width"], y["height"], *tb), expl)


class AudioFingerprintComparator(_Base):
    kind = K_AUDIO
    method_id = "audio-fingerprint-v1"
    no_data = "po jednej ze stron brak ścieżki audio z odciskiem (brak audio lub brak ffmpeg dla kontenera)"
    spec = AdapterSpec(id=method_id, kind=AdapterKind.comparator, version="1",
                       description="Odcisk spektralny audio z dowolnym przesunięciem; wspólny odcinek", extras=["perceptual"])

    def _best(self, ia, ib):
        best = None
        usable = False
        for x in ia:
            for y in ib:
                if not any(c == "1" for c in x["valid"]) or not any(c == "1" for c in y["valid"]):
                    continue
                usable = True
                r = M.audio_run(x, y, self.cfg)
                if r is None:
                    continue
                span = r["a"][1] - r["a"][0]
                sc = min(1.0, span / max(1, min(x["duration_ms"], y["duration_ms"]))) * max(0.0, 1 - r["ber"] / 0.5)
                if best is None or sc > best[0]:
                    best = (sc, x, y, r)
        if not usable:
            return None
        if best is None:
            x, y = ia[0], ib[0]
            return (0.0, [ArtifactRef(artifact_id=x["artifact_id"])], [ArtifactRef(artifact_id=y["artifact_id"])],
                    f"Odcisk audio: brak wspólnego odcinka >= {self.cfg['audio_window_hops'] * self.cfg['audio_hop_ms']} ms "
                    f"przy BER <= {self.cfg['audio_max_ber']}.")
        sc, x, y, r = best
        expl = (f"Wspólny odcinek audio: A {_fmt(r['a'])} odpowiada B {_fmt(r['b'])}; BER {r['ber']} "
                f"(losowo ~0.5, próg {self.cfg['audio_max_ber']}). Wynik = pokrycie krótszego * (1 - 2*BER) = {sc:.3f}.")
        return (sc, _span(x["artifact_id"], 1, 1, *r["a"]), _span(y["artifact_id"], 1, 1, *r["b"]), expl)


class TranscriptComparator(_Base):
    relation = Relation.shared_narrative
    kind = K_TRANSCRIPT
    method_id = "transcript-ngram-v1"
    no_data = "po jednej ze stron brak transkrypcji (brak backendu transkrypcji lub mowy)"
    spec = AdapterSpec(id=method_id, kind=AdapterKind.comparator, version="1",
                       description="Najdłuższy wspólny ciąg słów transkrypcji z przedziałem czasu i cytatem")

    def _best(self, ia, ib):
        best = None
        usable = False
        for x in ia:
            wa = M.words(x["segments"])
            for y in ib:
                wb = M.words(y["segments"])
                if len(wa) < self.cfg["transcript_ngram"] or len(wb) < self.cfg["transcript_ngram"]:
                    continue
                usable = True
                r = M.transcript_run(wa, wb, self.cfg)
                if r is None:
                    continue
                sc = min(1.0, r["words"] / max(1, min(len(wa), len(wb))))
                if best is None or sc > best[0]:
                    best = (sc, x, y, r)
        if not usable:
            return None
        if best is None:
            x, y = ia[0], ib[0]
            return (0.0, [ArtifactRef(artifact_id=x["artifact_id"])], [ArtifactRef(artifact_id=y["artifact_id"])],
                    f"Transkrypcje: brak wspólnego ciągu >= {self.cfg['transcript_min_words']} słów.")
        sc, x, y, r = best
        expl = (f"Wspólny cytat ({r['words']} słów): \"{r['quote']}\"; A {_fmt(r['a'])}, B {_fmt(r['b'])}. "
                f"Wynik = słowa wspólne / słowa krótszej transkrypcji = {sc:.3f}.")
        return (sc, _span(x["artifact_id"], 1, 1, *r["a"]), _span(y["artifact_id"], 1, 1, *r["b"]), expl)


class VoiceAuxComparator(_Base):
    relation = Relation.campaign_candidate
    kind = K_VOICE
    method_id = "voice-aux-v1"
    no_data = "po jednej ze stron za mało mowy na profil głosu"
    spec = AdapterSpec(id=method_id, kind=AdapterKind.comparator, version="1",
                       description="POMOCNICZY sygnał: podobieństwo barwy głosu (profil widmowy, nie model mówcy)")

    def _best(self, ia, ib):
        best = None
        for x in ia:
            for y in ib:
                c = M.cosine(x["mean"], y["mean"])
                if best is None or c > best[0]:
                    best = (c, x, y)
        c, x, y = best
        sc = max(0.0, c)
        expl = (f"SYGNAŁ POMOCNICZY (nie dowód tożsamości mówcy): kosinus profili widmowych głosu = {c:.3f} "
                f"(backend {x['backend']}, mowa A {x['voiced_s']} s, B {y['voiced_s']} s). Podobny głos może wynikać z "
                f"tego samego lektora lub syntezatora i nie przesądza o jednej kampanii.")
        return (sc, [ArtifactRef(artifact_id=x["artifact_id"])], [ArtifactRef(artifact_id=y["artifact_id"])], expl)


def get_adapters() -> list[Any]:
    return [VideoAudioExtractor(), VideoFramesComparator(), AudioFingerprintComparator(), TranscriptComparator(),
            VoiceAuxComparator()]


__all__ = ["VideoAudioExtractor", "VideoFramesComparator", "AudioFingerprintComparator", "TranscriptComparator",
           "VoiceAuxComparator", "get_adapters", "EXTRACTOR_VERSION", "load_config"]
