import json
import hashlib
import shutil
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import preflight_project
import reference_catalog


def _make_boardy_project(tmp_path):
    project = _make_project(tmp_path)
    Image.new("RGB", (2, 2)).save(project / "references/boardy.png")
    _write(project / "canon.json", {
        "version": 1, "locked_traits": [f"trait {i}" for i in range(5)],
        "allowed_variations": ["light"], "prohibitions": ["no logos"],
    })
    _write(project / "references.json", {"version": 1, "references": {"boardy": {
        "path": "references/boardy.png", "kind": "character", "status": "approved",
        "sha256": hashlib.sha256((project / "references/boardy.png").read_bytes()).hexdigest(),
    }}})
    batches = [{"id": f"set-{i:02}", "title": title, "order": i}
               for i, title in enumerate(reference_catalog.BOARDY_BATCH_TITLES, 1)]
    panels = []
    for number in range(1, 109):
        brief = f"briefs/{number:03}.txt"
        (project / brief).write_text(f"Scene {number}", encoding="utf-8")
        panels.append({"name": f"boardy-{number:03}", "scene_number": number,
                       "batch_id": batches[(number - 1) // 12]["id"],
                       "brief": brief, "references": ["boardy"], "seed": number})
    _write(project / "production.json", {"version": 2, "canon": "canon.json",
                                        "batches": batches, "panels": panels})
    return project


def _edit_json(path, edit):
    value = json.loads(path.read_text(encoding="utf-8"))
    edit(value)
    _write(path, value)


def test_boardy_preflight_reports_batches_hashes_without_services(tmp_path, monkeypatch):
    import direct_panel
    import make_panel
    def forbidden(*args, **kwargs):
        raise AssertionError("preflight must never contact production services")
    monkeypatch.setattr(direct_panel, "run_direction", forbidden)
    monkeypatch.setattr(make_panel, "make_panel", forbidden)
    project = _make_boardy_project(tmp_path)
    report = preflight_project.run_preflight(project)
    assert report["ok"]
    assert len(report["batches"]) == 9
    assert all(batch["expected_count"] == 12 and len(batch["scenes"]) == 12
               for batch in report["batches"])
    assert report["batches"][0]["title"] == "First contact"
    assert report["canon"]["sha256"] == hashlib.sha256((project / "canon.json").read_bytes()).hexdigest()
    assert len(report["panels"]) == 108


@pytest.mark.parametrize("problem,match", [
    ("missing_canon", "canon"), ("invalid_canon", "canon"),
    ("wrong_canon", "canon.json"), ("missing_image", "file"),
    ("draft", "not approved"), ("wrong_id", "boardy"),
    ("empty_brief", "brief is empty"), ("escaped", "escapes"),
    ("changed_image", "sha256"), ("missing_digest", "sha256"),
    ("invalid_image", "image"), ("wrong_kind", "character"),
])
def test_boardy_preflight_blocks_invalid_inputs(tmp_path, problem, match):
    project = _make_boardy_project(tmp_path)
    if problem == "missing_canon":
        (project / "canon.json").unlink()
    elif problem == "invalid_canon":
        _write(project / "canon.json", {"version": 999})
    elif problem == "wrong_canon":
        _edit_json(project / "production.json", lambda value: value.update(canon="other.json"))
    elif problem == "missing_image":
        (project / "references/boardy.png").unlink()
    elif problem == "draft":
        _edit_json(project / "references.json", lambda value: value["references"]["boardy"].update(status="draft"))
    elif problem == "wrong_id":
        _edit_json(project / "production.json", lambda value: value["panels"][0].update(references=["maya"]))
    elif problem == "empty_brief":
        (project / "briefs/001.txt").write_text("", encoding="utf-8")
    elif problem == "escaped":
        _edit_json(project / "production.json", lambda value: value.update(canon="../canon.json"))
    elif problem == "changed_image":
        Image.new("RGB", (2, 2), "red").save(project / "references/boardy.png")
    elif problem == "missing_digest":
        _edit_json(project / "references.json", lambda value: value["references"]["boardy"].pop("sha256"))
    elif problem == "invalid_image":
        (project / "references/boardy.png").write_bytes(b"not an image")
    elif problem == "wrong_kind":
        _edit_json(project / "references.json", lambda value: value["references"]["boardy"].update(kind="style"))
    report = preflight_project.run_preflight(project, allow_draft_references=True)
    assert not report["ok"]
    assert any(match in message for message in report["errors"]), report["errors"]


def test_boardy_preflight_aggregates_canon_catalog_and_manifest_errors(tmp_path):
    project = _make_boardy_project(tmp_path)
    (project / "canon.json").unlink()
    (project / "references/boardy.png").unlink()
    (project / "briefs/001.txt").write_text("", encoding="utf-8")
    report = preflight_project.run_preflight(project)
    assert not report["ok"]
    assert any("canon" in message for message in report["errors"])
    assert any("file does not exist" in message for message in report["errors"])
    assert any("brief is empty" in message for message in report["errors"])


@pytest.mark.parametrize("read_error", [PermissionError("access denied"), OSError("read failed")])
def test_boardy_preflight_aggregates_unreadable_reference(tmp_path, monkeypatch, read_error):
    project = _make_boardy_project(tmp_path)
    (project / "canon.json").unlink()
    reference_path = (project / "references/boardy.png").resolve()
    original_read_bytes = Path.read_bytes

    def read_bytes(path):
        if path.resolve() == reference_path:
            raise read_error
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    report = preflight_project.run_preflight(project)
    assert not report["ok"]
    assert any("canon.json" in message for message in report["errors"])
    assert any("reference 'boardy'" in message and "cannot read image file" in message
               for message in report["errors"])


def test_boardy_preflight_aggregates_corrupted_png_checksum(tmp_path):
    project = _make_boardy_project(tmp_path)
    (project / "canon.json").unlink()
    image_path = project / "references/boardy.png"
    data = bytearray(image_path.read_bytes())
    idat = data.index(b"IDAT")
    length = int.from_bytes(data[idat - 4:idat], "big")
    data[idat + 4 + length] ^= 1  # Damage IDAT CRC while keeping header readable.
    image_path.write_bytes(data)
    _edit_json(project / "references.json", lambda value: value["references"]["boardy"].update(
        sha256=hashlib.sha256(data).hexdigest(),
    ))
    report = preflight_project.run_preflight(project)
    assert not report["ok"]
    assert any("canon.json" in message for message in report["errors"])
    assert any("reference 'boardy': invalid supported image" in message
               for message in report["errors"])


def test_staged_boardy_inputs_preserve_all_source_descriptions(tmp_path):
    project = Path(__file__).resolve().parents[1] / "projects/boardy-108"
    manifest = reference_catalog.load_manifest(project)
    descriptions = [panel["brief"].read_text(encoding="utf-8").rstrip("\n")
                    for panel in manifest["panels"]]
    # Digest of the 108 original email descriptions in numbered order, joined
    # with LF (numbering prefixes removed, text otherwise unchanged).
    assert hashlib.sha256("\n".join(descriptions).encode("utf-8")).hexdigest() == (
        "961cb39184451850d83b67070b29631201135a7e8a33ced47a89421e292f96b3"
    )
    assert len(descriptions) == 108
    assert len(list((project / "briefs").glob("*.txt"))) == 108
    assert all(panel["references"] == ["boardy"] for panel in manifest["panels"])
    fixture = tmp_path / "staged-inputs"
    fixture.mkdir()
    shutil.copytree(project / "briefs", fixture / "briefs")
    for filename in ("canon.json", "production.json"):
        shutil.copyfile(project / filename, fixture / filename)
    _write(fixture / "references.json", {"version": 1, "references": {"boardy": {
        "path": "references/boardy-canonical.png", "kind": "character", "status": "draft",
    }}})
    report = preflight_project.run_preflight(fixture)
    assert not report["ok"]
    assert len(report["batches"]) == 9
    assert all(len(batch["scenes"]) == 12 for batch in report["batches"])
    assert any("not approved" in message for message in report["errors"])


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _make_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / "references").mkdir(parents=True)
    (project / "briefs").mkdir()
    (project / "requests").mkdir()
    (project / "references" / "maya.png").write_bytes(b"png")
    (project / "references" / "command-center.png").write_bytes(b"png")
    (project / "briefs" / "panel-01.txt").write_text("Maya orders a pause.", encoding="utf-8")
    return project


def test_preflight_passes_with_approved_references_and_valid_manifest(tmp_path):
    project = _make_project(tmp_path)
    _write(
        project / "references.json",
        {
            "version": 1,
            "references": {
                "maya": {"path": "references/maya.png", "kind": "character", "status": "approved"},
            },
        },
    )
    _write(
        project / "production.json",
        {
            "version": 1,
            "panels": [
                {"name": "panel-01", "brief": "briefs/panel-01.txt", "references": ["maya"], "seed": 1},
            ],
        },
    )

    report = preflight_project.run_preflight(project)

    assert report["ok"] is True
    assert report["errors"] == []
    assert report["panels"][0]["name"] == "panel-01"


def test_preflight_blocks_on_draft_reference(tmp_path):
    project = _make_project(tmp_path)
    _write(
        project / "references.json",
        {
            "version": 1,
            "references": {
                "maya": {"path": "references/maya.png", "kind": "character", "status": "draft"},
            },
        },
    )
    _write(
        project / "production.json",
        {
            "version": 1,
            "panels": [
                {"name": "panel-01", "brief": "briefs/panel-01.txt", "references": ["maya"], "seed": 1},
            ],
        },
    )

    report = preflight_project.run_preflight(project)

    assert report["ok"] is False
    assert any("not approved" in message for message in report["errors"])


def test_preflight_allow_draft_references_unblocks(tmp_path):
    project = _make_project(tmp_path)
    _write(
        project / "references.json",
        {
            "version": 1,
            "references": {
                "maya": {"path": "references/maya.png", "kind": "character", "status": "draft"},
            },
        },
    )
    _write(
        project / "production.json",
        {
            "version": 1,
            "panels": [
                {"name": "panel-01", "brief": "briefs/panel-01.txt", "references": ["maya"], "seed": 1},
            ],
        },
    )

    report = preflight_project.run_preflight(project, allow_draft_references=True)

    assert report["ok"] is True
    assert any("still draft" in message for message in report["warnings"])


def test_preflight_warns_about_unused_catalog_entry(tmp_path):
    project = _make_project(tmp_path)
    _write(
        project / "references.json",
        {
            "version": 1,
            "references": {
                "maya": {"path": "references/maya.png", "kind": "character", "status": "approved"},
                "command-center": {"path": "references/command-center.png", "kind": "location", "status": "approved"},
            },
        },
    )
    _write(
        project / "production.json",
        {
            "version": 1,
            "panels": [
                {"name": "panel-01", "brief": "briefs/panel-01.txt", "references": ["maya"], "seed": 1},
            ],
        },
    )

    report = preflight_project.run_preflight(project)

    assert any("command-center" in message and "not used" in message for message in report["warnings"])


def test_preflight_aggregates_manifest_and_catalog_errors_together(tmp_path):
    project = _make_project(tmp_path)
    _write(project / "references.json", {"version": 1, "references": {}})
    _write(
        project / "production.json",
        {
            "version": 1,
            "panels": [
                {"name": "panel-01", "brief": "briefs/panel-01.txt", "references": ["ghost"], "seed": 1},
            ],
        },
    )

    report = preflight_project.run_preflight(project)

    assert report["ok"] is False
    assert any("unknown reference ID" in message for message in report["errors"])
