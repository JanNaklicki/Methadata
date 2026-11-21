"""E16: podobieństwo wideo i audio (klatki, ujęcia, odcisk audio, transkrypcja; głos jako sygnał pomocniczy)."""
from .adapters import (EXTRACTOR_VERSION, AudioFingerprintComparator, TranscriptComparator, VideoAudioExtractor,
                       VideoFramesComparator, VoiceAuxComparator, get_adapters)
from .config import DEFAULTS, load_config

__all__ = ["VideoAudioExtractor", "VideoFramesComparator", "AudioFingerprintComparator", "TranscriptComparator",
           "VoiceAuxComparator", "get_adapters", "EXTRACTOR_VERSION", "DEFAULTS", "load_config"]
