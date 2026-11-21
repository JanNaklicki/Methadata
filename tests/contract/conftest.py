from pathlib import Path
import pytest

FIX = Path(__file__).resolve().parent.parent / "fixtures" / "contract"


@pytest.fixture
def fixtures_dir():
    return FIX
