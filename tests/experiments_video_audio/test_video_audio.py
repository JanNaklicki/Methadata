import pytest

for _m in ("numpy", "cv2", "PIL"):
    pytest.importorskip(_m)

from methadata.contracts import Artifact, ExtractionState, InputKind, Observation, ids
from methadata.contracts.models import ImageRect
from methadata.contracts.registry import discover
from methadata.devtools.realdata import BACKUP
from methadata.experiments.video_audio import (AudioFingerprintComparator, TranscriptComparator, VideoAudioExtractor,
                                               VideoFramesComparator, VoiceAuxComparator, bench)
from methadata.experiments.video_audio import match as M
from methadata.runner import run_experiment

MP4 = {}


def video(scenes, **kw):
    key = (tuple(scenes), tuple(sorted(kw.items())))
    if key not in MP4:
        MP4[key] = bench.synthetic_video(scenes, **kw)
    return MP4[key]


class Reader:
    def __init__(self): self.d = {}
    def open_bytes(self, ref): return self.d[ref.artifact_id]


def make(name, data, reader, role="video", media_type="video/mp4"):
    oid = ids.observation_id(name, "full_capture")
    sha = ids.sha256_bytes(data)
    a = Artifact(artifact_id=ids.artifact_id(oid, role, sha), observation_id=oid, role=role,
                 local_ref=f"{name}", sha256=sha, media_type=media_type)
    reader.d[a.artifact_id] = data
    return Observation(observation_id=oid, canonical_id="can-" + name, source_ref=name,
                       input_kind=InputKind.full_capture, artifacts=[a])


def feats(obs, reader, **kw):
    ext = VideoAudioExtractor(**kw)
    return {o.observation_id: list(ext.extract(o, reader=reader)[0]) for o in obs}


def cmp(comp, a, b, f):
    out = comp.compare(a, b, f, run_id="run-t")
    assert len(out) == 1
    return out[0]


def vid_pair(sa, sb, **kw):
    r = Reader()
    a, b = make("a", video(sa, **kw), r), make("b", video(sb, **kw), r)
    return a, b, feats([a, b], r)


# 1. wideo: klatki i ujęcia ---------------------------------------------------------------------

def test_short_fragment_inside_longer_creative():
    a, b, f = vid_pair([1, 2, 3, 4, 5, 6], [3, 4, 5])
    ev = cmp(VideoFramesComparator(), a, b, f)
    assert ev.score is not None and ev.score >= 0.95
    ra, rb = ev.evidence_refs["can-a"], ev.evidence_refs["can-b"]
    assert [r.locator.frame_ms for r in ra] == [4000, 10000]   # sceny 3..5 w A
    assert [r.locator.frame_ms for r in rb] == [0, 6000]
    assert all(isinstance(r.locator, ImageRect) for r in ra + rb)
    assert "A 4000-10000 ms" in ev.explanation and "B 0-6000 ms" in ev.explanation


def test_unrelated_videos_score_zero():
    a, b, f = vid_pair([1, 2, 3], [7, 8, 9])
    ev = cmp(VideoFramesComparator(), a, b, f)
    assert ev.score == 0.0


def test_shortened_montage_found_by_shots():
    # B = A bez scen 2 i 4: brak długiego ciągłego fragmentu klatek, ale ujęcia 1,3,5 w tej samej kolejności
    a, b, f = vid_pair([1, 2, 3, 4, 5], [1, 3, 5])
    ev = cmp(VideoFramesComparator(), a, b, f)
    assert ev.score is not None and ev.score >= 0.9
    assert "ujęcia: 3 wspólnych" in ev.explanation


def test_reencoded_with_noise_still_matches():
    r = Reader()
    a = make("a", video([1, 2, 3], seed=1), r)
    b = make("b", video([1, 2, 3], noise=12.0, seed=2), r)
    ev = cmp(VideoFramesComparator(), a, b, feats([a, b], r))
    assert ev.score is not None and ev.score >= 0.9


def test_fragment_starting_off_sampling_grid_still_matches():
    # fragment zaczyna się na granicy sceny (2 s = wielokrotność kroku próbkowania) - dopasowanie pełne
    a, b, f = vid_pair([1, 2, 3], [2, 3])
    assert cmp(VideoFramesComparator(), a, b, f).score >= 0.9


def test_known_failure_cropped_video():
    # Udokumentowane ograniczenie: globalny pHash klatek nie wykrywa kadrowania/zbliżenia (jak w E09);
    # wynik 0 mimo tej samej treści. Zmiana tempa jest wykrywana tylko przez sekwencję ujęć (nie klatki).
    r = Reader()
    a = make("a", video([1, 2, 3]), r)
    b = make("b", video([1, 2, 3], crop=0.6), r)
    assert cmp(VideoFramesComparator(), a, b, feats([a, b], r)).score == 0.0


def test_too_short_video_is_not_comparable():
    r = Reader()
    a = make("a", bench.synthetic_video([1], scene_s=1.0), r)
    b = make("b", bench.synthetic_video([1], scene_s=1.0), r)
    ev = cmp(VideoFramesComparator(), a, b, feats([a, b], r))
    assert ev.score is None and "brak podstaw" in ev.explanation


def test_video_without_audio_status_and_audio_comparison_not_applicable():
    r = Reader()
    a = make("a", video([1, 2, 3]), r)
    ext = VideoAudioExtractor()
    fs, sts = ext.extract(a, reader=r)
    assert [s.state for s in sts] == [ExtractionState.ok]
    assert "brak ścieżki audio" in sts[0].detail
    assert {f.kind for f in fs} == {"video"}
    ev = cmp(AudioFingerprintComparator(), a, a.model_copy(update={"canonical_id": "can-z"}),
             {a.observation_id: fs})
    assert ev.score is None


def test_corrupt_and_missing_inputs():
    r = Reader()
    bad = make("bad", b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64, r)
    _, sts = VideoAudioExtractor().extract(bad, reader=r)
    assert sts[0].state in (ExtractionState.unsupported, ExtractionState.failed)
    gone = make("gone", b"x", r)
    r.d.clear()
    _, sts = VideoAudioExtractor().extract(gone, reader=r)
    assert sts[0].state is ExtractionState.missing_input


# 2. audio ---------------------------------------------------------------------------------------

def wav_obs(name, wav, r):
    return make(name, wav, r, role="audio", media_type="audio/wav")


def test_audio_fragment_inside_longer_track():
    r = Reader()
    full = bench.synthetic_audio(1, 20)
    a, b = wav_obs("a", full, r), wav_obs("b", bench.wav_slice(full, 5, 12), r)
    ev = cmp(AudioFingerprintComparator(), a, b, feats([a, b], r))
    assert ev.score is not None and ev.score >= 0.9
    ra, rb = ev.evidence_refs["can-a"], ev.evidence_refs["can-b"]
    assert abs(ra[0].locator.frame_ms - 5000) <= 200 and abs(ra[1].locator.frame_ms - 12000) <= 300
    assert rb[0].locator.frame_ms <= 200


def test_different_audio_scores_zero():
    r = Reader()
    a, b = wav_obs("a", bench.synthetic_audio(1, 10), r), wav_obs("b", bench.synthetic_audio(2, 10), r)
    ev = cmp(AudioFingerprintComparator(), a, b, feats([a, b], r))
    assert ev.score == 0.0


def test_silent_audio_is_not_comparable():
    import io
    import wave
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b"\x00\x00" * 16000 * 5)
    r = Reader()
    a, c = wav_obs("a", b.getvalue(), r), wav_obs("b", b.getvalue(), r)
    fs, sts = VideoAudioExtractor().extract(a, reader=r)
    assert "audio ciche" in sts[0].detail
    ev = cmp(AudioFingerprintComparator(), a, c, feats([a, c], r))
    assert ev.score is None


def test_unsupported_audio_container_without_ffmpeg():
    from methadata.experiments.video_audio import audio as AU
    if AU.ffmpeg_available():
        pytest.skip("ffmpeg dostępny")
    r = Reader()
    mp3 = make("m", b"ID3\x03\x00\x00\x00\x00\x00\x00" + b"\x00" * 100, r, role="audio", media_type="audio/mpeg")
    fs, sts = VideoAudioExtractor().extract(mp3, reader=r)
    assert not fs and sts[0].state is ExtractionState.unsupported and "ffmpeg" in sts[0].detail


# 3. transkrypcja --------------------------------------------------------------------------------

def test_transcript_shared_quote_with_times():
    r = Reader()
    wa, wb = bench.synthetic_audio(1, 8), bench.synthetic_audio(2, 8)
    a, b = wav_obs("a", wa, r), wav_obs("b", wb, r)

    def ta(_): return [{"start_ms": 0, "end_ms": 4000, "text": "Witaj tutaj nowa oferta"},
                       {"start_ms": 4000, "end_ms": 8000, "text": "Zainwestuj dwieście złotych dziś a zarobisz tysiące"}]

    def tb(_): return [{"start_ms": 0, "end_ms": 3000, "text": "Uwaga"},
                       {"start_ms": 3000, "end_ms": 7000, "text": "zainwestuj dwieście złotych dziś a zarobisz"}]
    f = {**feats([a], r, transcriber=ta), **feats([b], r, transcriber=tb)}
    ev = cmp(TranscriptComparator(), a, b, f)
    assert ev.score is not None and ev.score > 0.4
    assert "zainwestuj dwieście złotych dziś a zarobisz" in ev.explanation
    assert ev.evidence_refs["can-a"][0].locator.frame_ms >= 4000
    assert ev.evidence_refs["can-b"][0].locator.frame_ms >= 3000


def test_transcript_without_backend_not_comparable():
    r = Reader()
    a, b = wav_obs("a", bench.synthetic_audio(1, 6), r), wav_obs("b", bench.synthetic_audio(1, 6), r)
    f = feats([a, b], r)
    _, sts = VideoAudioExtractor().extract(a, reader=r)
    assert "transkrypcja: brak backendu" in sts[0].detail
    assert cmp(TranscriptComparator(), a, b, f).score is None


def test_transcript_run_logic():
    segs = [{"start_ms": 0, "end_ms": 1000, "text": "a b c d e f g h"}]
    segs2 = [{"start_ms": 500, "end_ms": 1500, "text": "x a b c d e f g y"}]
    r = M.transcript_run(M.words(segs), M.words(segs2), {"transcript_ngram": 4, "transcript_min_words": 6})
    assert r["words"] == 7 and r["quote"] == "a b c d e f g"


# 4. głos (sygnał pomocniczy) --------------------------------------------------------------------

def test_voice_is_separate_auxiliary_signal():
    r = Reader()
    a = wav_obs("a", bench.synthetic_audio(1, 10, voice_seed=50), r)
    same = wav_obs("same", bench.synthetic_audio(2, 10, voice_seed=50), r)
    other = wav_obs("other", bench.synthetic_audio(3, 10, voice_seed=99), r)
    f = feats([a, same, other], r)
    v = VoiceAuxComparator()
    s_same, s_other = cmp(v, a, same, f), cmp(v, a, other, f)
    assert s_same.score > s_other.score
    assert s_same.method_id == "voice-aux-v1" and s_same.relation.value == "campaign_candidate"
    assert "POMOCNICZY" in s_same.explanation
    # różna treść, ta sama barwa: odcisk audio nie wykrywa zgodności - głos nie wpływa na inne metody
    assert cmp(AudioFingerprintComparator(), a, same, f).score == 0.0


# 5. rejestracja i runner ------------------------------------------------------------------------

def test_discover_and_runner():
    found = discover()
    for k in ("video-audio-extractor", "video-frames-v1", "audio-fingerprint-v1", "transcript-ngram-v1", "voice-aux-v1"):
        assert k in found
    from methadata.contracts import AdapterKind
    r = Reader()
    obs = [make("a", video([1, 2, 3, 4]), r), make("frag", video([2, 3]), r), make("foreign", video([7, 8, 9]), r)]
    cl = next(v for v in found.values() if v.spec.kind is AdapterKind.clusterer)
    cfg = {"dataset_version": "t", "seed": 0, "relation": "shared_template", "clusterer": cl.spec.id,
           "extractors": ["video-audio-extractor"], "comparators": ["video-frames-v1"],
           "cluster_config": {"threshold": 0.5, "similarity_version": "va-1", "dataset_version": "t"}}
    res = run_experiment(cfg, obs, found, reader=r)
    sc = {frozenset(p.pair): p.score for p in res["bundle"].pairs}
    assert sc[frozenset({"can-a", "can-frag"})] >= 0.9
    assert sc[frozenset({"can-a", "can-foreign"})] == 0.0


# 6. dane rzeczywiste (próbka kontrolna, osobno) --------------------------------------------------

def test_real_data_video_control_sample():
    if not BACKUP.is_dir():
        pytest.skip("brak katalogu BACKUP")
    files = sorted(BACKUP.rglob("*.mp4"))[:2]
    if not files:
        pytest.skip("brak plików wideo w BACKUP")
    r = Reader()
    obs = [make(p.stem, p.read_bytes(), r) for p in files]
    f = feats(obs, r)
    assert all(any(x.kind == "video" for x in v) for v in f.values())
