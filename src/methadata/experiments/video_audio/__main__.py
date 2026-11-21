"""python -m methadata.experiments.video_audio demo [--config PLIK.json] [--out DIR]  (próbka SYNTETYCZNA)"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from methadata.contracts import Artifact, InputKind, Observation, ids

from . import bench
from .adapters import (AudioFingerprintComparator, VideoAudioExtractor, VideoFramesComparator, VoiceAuxComparator)
from .config import load_config


class _R:
    def __init__(self): self.d = {}
    def open_bytes(self, ref): return self.d[ref.artifact_id]


def _obs(name, data, r, role, mt):
    oid = ids.observation_id(name, "full_capture")
    sha = ids.sha256_bytes(data)
    a = Artifact(artifact_id=ids.artifact_id(oid, role, sha), observation_id=oid, role=role, local_ref=name,
                 sha256=sha, media_type=mt)
    r.d[a.artifact_id] = data
    return Observation(observation_id=oid, canonical_id="can-" + name, source_ref=name,
                       input_kind=InputKind.full_capture, artifacts=[a])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="methadata.experiments.video_audio")
    ap.add_argument("cmd", choices=["demo"])
    ap.add_argument("--config")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    cfg = load_config(a.config)
    r = _R()
    ext = VideoAudioExtractor(cfg)
    cases = []

    def run(label, comp, o1, o2):
        f = {o.observation_id: list(ext.extract(o, reader=r)[0]) for o in (o1, o2)}
        ev = comp.compare(o1, o2, f, run_id="run-demo")[0]
        cases.append({"case": label, "method": ev.method_id, "score": ev.score, "explanation": ev.explanation,
                      "refs": {k: [x.locator.frame_ms if x.locator else None for x in v] for k, v in ev.evidence_refs.items()}})
    vid = lambda n, s, **k: _obs(n, bench.synthetic_video(s, **k), r, "video", "video/mp4")  # noqa: E731
    wav = lambda n, w: _obs(n, w, r, "audio", "audio/wav")  # noqa: E731
    vf = VideoFramesComparator(cfg)
    run("fragment 3 scen w dłuższej kreacji", vf, vid("a", [1, 2, 3, 4, 5, 6]), vid("b", [3, 4, 5]))
    run("skrócony montaż (bez scen 2 i 4)", vf, vid("c", [1, 2, 3, 4, 5]), vid("d", [1, 3, 5]))
    run("PRZYPADEK ZAWODNOŚCI: kadrowanie 0.6", vf, vid("e", [1, 2, 3]), vid("f", [1, 2, 3], crop=0.6))
    full = bench.synthetic_audio(1, 20)
    af = AudioFingerprintComparator(cfg)
    run("audio: wycinek 5-12 s", af, wav("g", full), wav("h", bench.wav_slice(full, 5, 12)))
    run("głos: ta sama barwa, inna treść (pomocniczy)", VoiceAuxComparator(cfg),
        wav("i", bench.synthetic_audio(1, 10, voice_seed=50)), wav("j", bench.synthetic_audio(2, 10, voice_seed=50)))
    out = {"config": cfg, "sample": "syntetyczna", "cases": cases}
    text = json.dumps(out, ensure_ascii=False, indent=2)
    if a.out:
        Path(a.out).mkdir(parents=True, exist_ok=True)
        (Path(a.out) / "demo.json").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
