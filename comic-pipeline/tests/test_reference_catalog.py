import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import reference_catalog


BOARDY_TITLES = (
    "First contact", "Cartography", "Work", "Curiosity", "Memory",
    "Misreadings", "Idle", "Craft", "The box",
)


def _make_batched_project(tmp_path):
    project = _make_project(tmp_path)
    batches = [
        {"id": f"set-{index:02d}", "title": title, "order": index}
        for index, title in enumerate(BOARDY_TITLES, 1)
    ]
    panels = []
    for number in range(1, 109):
        brief = f"briefs/{number:03d}.txt"
        (project / brief).write_text(f"Original scene {number}.", encoding="utf-8")
        panels.append({
            "name": f"boardy-{number:03d}", "scene_number": number,
            "batch_id": batches[(number - 1) // 12]["id"],
            "brief": brief, "references": ["boardy"], "seed": 108000 + number,
        })
    data = {"version": 2, "canon": "canon.json", "batches": batches, "panels": panels}
    _write(project / "production.json", data)
    return project, data


def test_load_version_two_normalizes_paths_and_preserves_order(tmp_path):
    project, data = _make_batched_project(tmp_path)
    manifest = reference_catalog.load_manifest(project)
    assert manifest["canon"] == (project / "canon.json").resolve()
    assert manifest["batches"] == data["batches"]
    assert [panel["scene_number"] for panel in manifest["panels"]] == list(range(1, 109))
    for source, panel in zip(data["panels"], manifest["panels"]):
        assert panel == {
            **source, "brief": (project / source["brief"]).resolve(),
            "width": 1024, "height": 1536, "max_render_references": 3,
        }


def test_version_one_normalized_contract_is_unchanged(tmp_path):
    project = _make_project(tmp_path)
    _write(project / "production.json", {"version": 1, "panels": [{
        "name": "panel-01", "brief": "briefs/panel-01.txt", "references": ["maya"], "seed": 42,
    }]})
    assert reference_catalog.load_manifest(project) == {
        "defaults": {"width": 1024, "height": 1536, "max_render_references": 3},
        "panels": [{"name": "panel-01", "brief": (project / "briefs/panel-01.txt").resolve(),
                    "references": ["maya"], "seed": 42, "width": 1024,
                    "height": 1536, "max_render_references": 3}],
    }


@pytest.mark.parametrize("mutation, message", [
    (lambda d: d["panels"][0].update(batch_id="missing"), "unknown batch"),
    (lambda d: d["panels"].pop(), "108"),
    (lambda d: d["panels"].append(dict(d["panels"][-1])), "108"),
    (lambda d: d["panels"][1].update(scene_number=1), "duplicate scene"),
    (lambda d: d["panels"][0].update(scene_number=109), "1–108"),
    (lambda d: d["panels"].reverse(), "scene order"),
    (lambda d: d["panels"][12].update(batch_id="set-01"), "twelve"),
    (lambda d: [p.update(batch_id="set-02") for p in d["panels"][:12]], "twelve"),
    (lambda d: d["batches"][1].update(id="set-01"), "duplicate batch ID"),
    (lambda d: d["batches"][1].update(order=1), "duplicate batch order"),
    (lambda d: d["batches"][0].update(title="Wrong"), "title"),
    (lambda d: d["batches"].reverse(), "batch order"),
    (lambda d: d["batches"].pop(), "nine"),
    (lambda d: d.update(canon="../canon.json"), "escapes"),
    (lambda d: d["panels"][0].update(brief="../outside.txt"), "escapes"),
    (lambda d: d["panels"][0].pop("scene_number"), "scene_number"),
    (lambda d: d["panels"][0].pop("batch_id"), "batch_id"),
    (lambda d: d["panels"][0].update(name="boardy-002"), "duplicate panel name"),
    (lambda d: d["panels"][0].update(references=["boardy", "boardy"]), "duplicate reference"),
])
def test_version_two_rejects_broken_scene_and_batch_integrity(tmp_path, mutation, message):
    project, data = _make_batched_project(tmp_path)
    mutation(data)
    _write(project / "production.json", data)
    with pytest.raises(reference_catalog.CatalogError, match=message):
        reference_catalog.load_manifest(project)


@pytest.mark.parametrize("target, field, value", [
    ("manifest", "version", 2.0), ("manifest", "canon", " "),
    ("manifest", "defaults", []), ("manifest", "batches", {}),
    ("batch", "id", []), ("batch", "order", True), ("batch", "title", None),
    ("panel", "scene_number", True), ("panel", "batch_id", {}),
    ("panel", "references", [{}]), ("panel", "references", [1]),
    ("panel", "width", True), ("panel", "height", 1.5),
    ("panel", "seed", True), ("panel", "seed", -1),
    ("panel", "max_render_references", "3"), ("panel", "max_render_references", 0),
    ("defaults", "width", False), ("defaults", "max_render_references", -1),
])
def test_version_two_reports_invalid_types_as_catalog_errors(tmp_path, target, field, value):
    project, data = _make_batched_project(tmp_path)
    objects = {"manifest": data, "batch": data["batches"][0],
               "panel": data["panels"][0], "defaults": data.setdefault("defaults", {})}
    objects[target][field] = value
    _write(project / "production.json", data)
    with pytest.raises(reference_catalog.CatalogError):
        reference_catalog.load_manifest(project)


@pytest.mark.parametrize("collection", ["batches", "panels"])
def test_version_two_rejects_non_object_records(tmp_path, collection):
    project, data = _make_batched_project(tmp_path)
    data[collection][0] = None
    _write(project / "production.json", data)
    with pytest.raises(reference_catalog.CatalogError, match="must be an object"):
        reference_catalog.load_manifest(project)


def test_version_two_keeps_per_scene_settings_over_defaults(tmp_path):
    project, data = _make_batched_project(tmp_path)
    data["defaults"] = {"width": 512, "height": 768, "max_render_references": 1}
    data["panels"][0].update(width=1536, height=1024, max_render_references=2)
    _write(project / "production.json", data)
    manifest = reference_catalog.load_manifest(project)
    assert manifest["panels"][0]["width"] == 1536
    assert manifest["panels"][0]["height"] == 1024
    assert manifest["panels"][0]["max_render_references"] == 2
    assert manifest["panels"][1]["width"] == 512
    assert manifest["panels"][1]["height"] == 768
    assert manifest["panels"][1]["max_render_references"] == 1


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _make_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / "references").mkdir(parents=True)
    (project / "briefs").mkdir()
    (project / "references" / "maya.png").write_bytes(b"png")
    (project / "references" / "command-center.png").write_bytes(b"png")
    (project / "briefs" / "panel-01.txt").write_text("Maya orders a pause.", encoding="utf-8")
    return project


def test_load_catalog_resolves_approved_entries(tmp_path):
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

    catalog = reference_catalog.load_catalog(project)

    assert catalog["maya"]["file"] == (project / "references" / "maya.png").resolve()
    assert catalog["maya"]["status"] == "approved"


def test_load_catalog_rejects_absolute_path(tmp_path):
    project = _make_project(tmp_path)
    _write(
        project / "references.json",
        {
            "version": 1,
            "references": {
                "maya": {"path": "C:/elsewhere/maya.png", "kind": "character", "status": "approved"},
            },
        },
    )

    with pytest.raises(reference_catalog.CatalogError, match="absolute"):
        reference_catalog.load_catalog(project)


def test_load_catalog_rejects_path_escaping_project(tmp_path):
    project = _make_project(tmp_path)
    _write(
        project / "references.json",
        {
            "version": 1,
            "references": {
                "maya": {"path": "../outside.png", "kind": "character", "status": "approved"},
            },
        },
    )

    with pytest.raises(reference_catalog.CatalogError, match="escapes the project directory"):
        reference_catalog.load_catalog(project)


def test_load_catalog_rejects_invalid_kind_and_status(tmp_path):
    project = _make_project(tmp_path)
    _write(
        project / "references.json",
        {
            "version": 1,
            "references": {
                "maya": {"path": "references/maya.png", "kind": "hero", "status": "final"},
            },
        },
    )

    with pytest.raises(reference_catalog.CatalogError) as excinfo:
        reference_catalog.load_catalog(project)
    assert any("kind" in message for message in excinfo.value.errors)
    assert any("status" in message for message in excinfo.value.errors)


def test_load_catalog_reports_missing_file(tmp_path):
    project = _make_project(tmp_path)
    _write(
        project / "references.json",
        {
            "version": 1,
            "references": {
                "ghost": {"path": "references/ghost.png", "kind": "character", "status": "approved"},
            },
        },
    )

    with pytest.raises(reference_catalog.CatalogError, match="does not exist"):
        reference_catalog.load_catalog(project)


def test_load_catalog_allows_draft_placeholder_without_file(tmp_path):
    project = _make_project(tmp_path)
    _write(
        project / "references.json",
        {
            "version": 1,
            "references": {
                "ghost": {"path": "references/not-made-yet.png", "kind": "location", "status": "draft"},
            },
        },
    )

    catalog = reference_catalog.load_catalog(project)

    assert catalog["ghost"]["status"] == "draft"
    assert not catalog["ghost"]["file"].exists()


def test_resolve_panel_references_rejects_draft_with_no_file_even_when_allowed(tmp_path):
    project = _make_project(tmp_path)
    _write(
        project / "references.json",
        {
            "version": 1,
            "references": {
                "ghost": {"path": "references/not-made-yet.png", "kind": "location", "status": "draft"},
            },
        },
    )
    catalog = reference_catalog.load_catalog(project)
    panel = {"name": "panel-01", "references": ["ghost"]}

    with pytest.raises(reference_catalog.CatalogError, match="no asset file yet"):
        reference_catalog.resolve_panel_references(panel, catalog, allow_draft=True)


def test_load_manifest_resolves_defaults_and_panels(tmp_path):
    project = _make_project(tmp_path)
    _write(
        project / "production.json",
        {
            "version": 1,
            "defaults": {"width": 1536, "height": 1024, "max_render_references": 3},
            "panels": [
                {
                    "name": "panel-01",
                    "brief": "briefs/panel-01.txt",
                    "references": ["maya", "command-center"],
                    "seed": 42,
                }
            ],
        },
    )

    manifest = reference_catalog.load_manifest(project)

    assert manifest["defaults"]["width"] == 1536
    panel = manifest["panels"][0]
    assert panel["name"] == "panel-01"
    assert panel["references"] == ["maya", "command-center"]
    assert panel["width"] == 1536
    assert panel["height"] == 1024


def test_load_manifest_rejects_duplicate_panel_names(tmp_path):
    project = _make_project(tmp_path)
    panel = {
        "name": "panel-01",
        "brief": "briefs/panel-01.txt",
        "references": ["maya"],
        "seed": 1,
    }
    _write(project / "production.json", {"version": 1, "panels": [panel, dict(panel)]})

    with pytest.raises(reference_catalog.CatalogError, match="duplicate panel name"):
        reference_catalog.load_manifest(project)


def test_load_manifest_rejects_duplicate_reference_within_panel(tmp_path):
    project = _make_project(tmp_path)
    _write(
        project / "production.json",
        {
            "version": 1,
            "panels": [
                {
                    "name": "panel-01",
                    "brief": "briefs/panel-01.txt",
                    "references": ["maya", "maya"],
                    "seed": 1,
                }
            ],
        },
    )

    with pytest.raises(reference_catalog.CatalogError, match="duplicate reference ID"):
        reference_catalog.load_manifest(project)


def test_load_manifest_rejects_reference_count_over_max(tmp_path):
    project = _make_project(tmp_path)
    _write(
        project / "production.json",
        {
            "version": 1,
            "defaults": {"max_render_references": 1},
            "panels": [
                {
                    "name": "panel-01",
                    "brief": "briefs/panel-01.txt",
                    "references": ["maya", "command-center"],
                    "seed": 1,
                }
            ],
        },
    )

    with pytest.raises(reference_catalog.CatalogError, match="exceeds the configured maximum"):
        reference_catalog.load_manifest(project)


def test_load_manifest_aggregates_multiple_errors(tmp_path):
    project = _make_project(tmp_path)
    _write(
        project / "production.json",
        {
            "version": 1,
            "panels": [
                {"name": "panel-01", "brief": "briefs/missing.txt", "references": [], "seed": -1},
            ],
        },
    )

    with pytest.raises(reference_catalog.CatalogError) as excinfo:
        reference_catalog.load_manifest(project)
    assert len(excinfo.value.errors) >= 3


def test_resolve_panel_references_rejects_unknown_id(tmp_path):
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
    catalog = reference_catalog.load_catalog(project)
    panel = {"name": "panel-01", "references": ["maya", "ghost"]}

    with pytest.raises(reference_catalog.CatalogError, match="unknown reference ID"):
        reference_catalog.resolve_panel_references(panel, catalog)


def test_resolve_panel_references_rejects_draft_unless_allowed(tmp_path):
    project = _make_project(tmp_path)
    _write(
        project / "references.json",
        {
            "version": 1,
            "references": {
                "command-center": {"path": "references/command-center.png", "kind": "location", "status": "draft"},
            },
        },
    )
    catalog = reference_catalog.load_catalog(project)
    panel = {"name": "panel-01", "references": ["command-center"]}

    with pytest.raises(reference_catalog.CatalogError, match="not approved"):
        reference_catalog.resolve_panel_references(panel, catalog)

    resolved = reference_catalog.resolve_panel_references(panel, catalog, allow_draft=True)
    assert resolved == [catalog["command-center"]["file"]]


def test_resolve_panel_references_returns_ordered_paths(tmp_path):
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
    catalog = reference_catalog.load_catalog(project)
    panel = {"name": "panel-01", "references": ["command-center", "maya"]}

    resolved = reference_catalog.resolve_panel_references(panel, catalog)

    assert resolved == [catalog["command-center"]["file"], catalog["maya"]["file"]]
