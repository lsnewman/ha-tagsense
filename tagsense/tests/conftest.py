import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DEFAULT_TESTDATA = Path(__file__).resolve().parents[2] / "test-frames"


@pytest.fixture(scope="session")
def testdata() -> Path:
    p = Path(os.environ.get("TAGSENSE_TESTDATA", DEFAULT_TESTDATA))
    if not p.is_dir():
        pytest.skip(f"no test frames at {p} (set TAGSENSE_TESTDATA)")
    return p
