import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import canon


def test_boardy_official_source_canon_and_approval():
    project = Path(__file__).resolve().parents[1] / "projects" / "boardy-108"
    value = canon.load_canon(project)
    text = canon.format_instructions(value)
    assert "red tie" in text
    assert "dark blue circular eyes" in text
    assert "curved smile" in text
    assert "must never show eyebrows" in text
    assert "No eyebrows in any still image" in text
    assert "no tie" not in text and "no eyes" not in text
    catalog = json.loads((project / "references.json").read_text())
    reference = catalog["references"]["boardy"]
    assert reference["status"] == "approved"
    assert reference["sha256"] == hashlib.sha256((project / reference["path"]).read_bytes()).hexdigest()
    assert reference["sha256"] == "85cecb75e26d98e765b473c7c762a919af7340f892ee6014925ceb22e3648bc5"


VALID_CANON = {
    "version": 1,
    "locked_traits": [
        "Male humanoid; fitted blue suit, white shirt, and red tie matching the approved official-page reference.",
        "Matte corrugated cardboard box with visible seams, packing tape, and imperfect edges.",
        "Simple dark blue circular eyes and friendly dark blue curved smile matching the approved official-page reference; no nose or additional facial features.",
        "Still images must never show eyebrows, floating brows, or brow-like marks. Preserve the official-page face with eyes and smile only.",
        "Identical proportions and silhouette.",
    ],
    "allowed_variations": ["environment", "weather", "light", "scale", "era", "medium", "camera angle", "alone/with people"],
    "prohibitions": ["Original work only; no licensed characters, brands, or logos.", "No eyebrows in any still image, even when a scene brief or prop suggests them."],
}


def write_canon(tmp_path, value):
    source = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
    (tmp_path / "canon.json").write_bytes(source)
    return source


def test_load_canon_preserves_fields_and_hashes_exact_source_bytes(tmp_path):
    source = write_canon(tmp_path, VALID_CANON)
    loaded = canon.load_canon(str(tmp_path))
    assert loaded == {**VALID_CANON, "sha256": hashlib.sha256(source).hexdigest()}
    assert canon.load_canon(tmp_path) == loaded
    (tmp_path / "canon.json").write_bytes(source + b"\n")
    assert canon.load_canon(tmp_path)["sha256"] != loaded["sha256"]


def test_format_instructions_preserves_order_and_all_sections(tmp_path):
    write_canon(tmp_path, VALID_CANON)
    loaded = canon.load_canon(tmp_path)
    instructions = canon.format_instructions(loaded)
    assert instructions == canon.format_instructions(loaded)
    for field, heading in [("locked_traits", "LOCKED TRAITS:"), ("allowed_variations", "ALLOWED VARIATIONS:"), ("prohibitions", "PROHIBITIONS:")]:
        assert heading in instructions
        positions = [instructions.index(item) for item in VALID_CANON[field]]
        assert positions == sorted(positions)
    assert "preserve the original scene brief" in instructions


@pytest.mark.parametrize("field,value", [
    ("version", 2), ("version", True), ("version", "1"),
    ("locked_traits", VALID_CANON["locked_traits"][:-1]),
    ("locked_traits", "not an array"), ("locked_traits", ["x"] * 5),
    ("prohibitions", []), ("prohibitions", [""]), ("prohibitions", ["   "]),
    ("prohibitions", ["a", "a"]), ("prohibitions", [1]),
    ("allowed_variations", []), ("allowed_variations", ["a", " a "]),
])
def test_load_canon_rejects_invalid_fields(tmp_path, field, value):
    write_canon(tmp_path, {**VALID_CANON, field: value})
    with pytest.raises(ValueError, match=field):
        canon.load_canon(tmp_path)


@pytest.mark.parametrize("value", [[], {key: value for key, value in VALID_CANON.items() if key != "prohibitions"}, {**VALID_CANON, "extra": "x"}])
def test_load_canon_rejects_wrong_shape(tmp_path, value):
    write_canon(tmp_path, value)
    with pytest.raises(ValueError):
        canon.load_canon(tmp_path)


@pytest.mark.parametrize("source", [b"not-json", b"\xff", b'{"version": 1, "version": 2}'])
def test_load_canon_rejects_malformed_source(tmp_path, source):
    (tmp_path / "canon.json").write_bytes(source)
    with pytest.raises(ValueError):
        canon.load_canon(tmp_path)
