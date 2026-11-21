"""Moduł E16 musi dać się zaimportować i odkryć bez numpy/cv2/PIL."""
import subprocess
import sys

CODE = """
import sys
for m in ('numpy', 'cv2', 'PIL'):
    sys.modules[m] = None
from methadata.contracts.registry import discover
found = discover()
need = {'video-audio-extractor', 'video-frames-v1', 'audio-fingerprint-v1', 'transcript-ngram-v1', 'voice-aux-v1'}
assert need <= set(found), sorted(found)
print('OK')
"""


def test_discover_without_numpy_cv2():
    r = subprocess.run([sys.executable, "-I", "-c", CODE], capture_output=True, text=True)
    assert r.returncode == 0 and "OK" in r.stdout, r.stderr[-2000:]
