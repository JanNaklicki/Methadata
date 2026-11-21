"""Progi E16. Jedyne źródło wartości domyślnych; `configs/video_audio/video_audio.json` je odzwierciedla."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

DEFAULTS: dict[str, Any] = {
    "frame_interval_ms": 500,     # co ile ms pobierana jest klatka (te same chwile dla obu stron => przekątne)
    "max_frames": 240,            # maks. klatek na wideo (sufit kosztu)
    "max_bytes": 268435456,       # większych plików nie dekodujemy (niezaufane dane)
    "hash_threshold": 10,         # odległość Hamminga pHash (z 64 b) uznawana za tę samą klatkę
    "flat_std": 2.0,              # klatka o odchyleniu jasności poniżej tego (czarna, jednolita) nie ma hasha
    "min_run_frames": 3,          # minimalna liczba kolejnych klatek wspólnego fragmentu
    "gap_tolerance": 1,           # ile kolejnych klatek może się nie zgadzać wewnątrz fragmentu
    "shot_cut_threshold": 20,     # skok pHash między sąsiednimi klatkami uznawany za cięcie
    "min_shot_matches": 2,        # minimalna liczba wspólnych ujęć (w tej samej kolejności)
    "audio_hop_ms": 100,          # krok odcisku audio
    "audio_window_hops": 20,      # okno oceny dopasowania audio (2 s)
    "audio_max_ber": 0.30,        # maks. średni odsetek różnych bitów w oknie (losowo ~0.5)
    "audio_max_seconds": 120,     # maks. długość analizowanego audio
    "audio_silence_rms": 0.002,   # ramka cichsza od tego nie ma odcisku
    "transcript_ngram": 4,        # długość n-gramu słów do indeksu transkrypcji
    "transcript_min_words": 6,    # minimalna długość wspólnego fragmentu transkrypcji (słowa)
    "voice_min_voiced_s": 3.0,    # minimalna ilość mowy (głośnych ramek) do profilu głosu
    "max_items": 4,               # maks. nagrań porównywanych na obserwację
}


def load_config(path: str | Path | None = None, override: Mapping[str, Any] | None = None) -> dict[str, Any]:
    cfg = dict(DEFAULTS)
    if path:
        cfg.update(json.loads(Path(path).read_text(encoding="utf-8")))
    cfg.update(override or {})
    return cfg
