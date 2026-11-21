import subprocess
import sys


def test_discover_strict_without_leidenalg():
    code = ("import sys\nsys.modules['leidenalg']=None\nsys.modules['igraph']=None\n"
            "from methadata.contracts import discover\n"
            "a=discover()\nassert 'leiden-v1' in a and len(a)>5\n"
            "import pytest\n"
            "from methadata.clustering.leiden import resolve_config, build_result\n"
            "try:\n build_result([], [], {'similarity_version':'v','dataset_version':'d'})\n print('ok-empty')\n"
            "except RuntimeError as e:\n print('err', 'graph' in str(e))\n")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
