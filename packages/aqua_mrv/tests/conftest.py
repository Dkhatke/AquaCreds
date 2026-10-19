import json
from pathlib import Path
from typing import Any

import pytest

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "bhitarkanika_example.json"


@pytest.fixture
def example_doc() -> dict[str, Any]:
    doc: dict[str, Any] = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    return doc


@pytest.fixture
def example_path() -> Path:
    return EXAMPLE
