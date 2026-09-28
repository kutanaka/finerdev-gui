from pathlib import Path

import pytest


@pytest.fixture
def write_layout(tmp_path: Path):
    def _write(source: str, filename: str = "layout.py") -> Path:
        path = tmp_path / filename
        path.write_text(source)
        return path

    return _write
