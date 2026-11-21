"""Moduł E09 musi dać się zaimportować i odkryć bez numpy/cv2 (strict discover nie może się wywrócić)."""
import subprocess
import sys

CODE = """
import sys
sys.modules['numpy'] = None
sys.modules['cv2'] = None
from methadata.contracts.registry import discover
found = discover()
need = {'perceptual-extractor', 'perceptual-phash-v1', 'perceptual-keypoints-v1'}
assert need <= set(found), sorted(found)
print('OK')
"""


def test_discover_without_numpy_cv2():
    r = subprocess.run([sys.executable, "-I", "-c", CODE], capture_output=True, text=True)
    assert r.returncode == 0 and "OK" in r.stdout, r.stderr[-2000:]
