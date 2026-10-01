import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import render_project
import reference_catalog


def test_one_panel_helper_success_and_failure(tmp_path):
    project = _make_project(tmp_path, ["panel-01"])
    panel = reference_catalog.load_manifest(project)["panels"][0]
    references = [project / "references" / "maya.png"]
    metadata = render_project.render_one_panel(project, panel, references, make_panel_fn=lambda *a, **k: [])
    assert metadata["name"] == "panel-01"
    assert metadata["references"][0]["id"] == "maya"
    def fail(*args, **kwargs):
        raise RuntimeError("test failure")
    with pytest.raises(RuntimeError, match="test failure"):
        render_project.render_one_panel(project, panel, references, make_panel_fn=fail)
    assert json.loads((project / "requests" / "panel-01-failure.json").read_text())["stage"] == "make_panel"


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _make_project(tmp_path: Path, panel_names: list[str]) -> Path:
    project = tmp_path / "project"
    (project / "references").mkdir(parents=True)
    (project / "briefs").mkdir()
    (project / "requests").mkdir()
    (project / "outputs").mkdir()
    (project / "references" / "maya.png").write_bytes(b"png-bytes")
    _write(
        project / "references.json",
        {
            "version": 1,
            "references": {
                "maya": {"path": "references/maya.png", "kind": "character", "status": "approved"},
            },
        },
    )
    panels = []
    for name in panel_names:
        (project / "briefs" / f"{name}.txt").write_text(f"Brief for {name}.", encoding="utf-8")
        panels.append(
            {
                "name": name,
                "brief": f"briefs/{name}.txt",
                "references": ["maya"],
                "seed": 1,
            }
        )
    _write(project / "production.json", {"version": 1, "panels": panels})
    return project


def test_dry_run_reports_order_without_calling_make_panel(tmp_path):
    project = _make_project(tmp_path, ["panel-01", "panel-02"])
    calls = []

    rendered = render_project.render_project(
        project,
        dry_run=True,
        make_panel_fn=lambda *a, **k: calls.append((a, k)) or [],
        log=lambda _message: None,
    )

    assert rendered == []
    assert calls == []


def test_renders_each_panel_and_writes_production_metadata(tmp_path):
    project = _make_project(tmp_path, ["panel-01", "panel-02"])
    calls = []

    def fake_make_panel(project_dir, name, references, **kwargs):
        calls.append((name, references, kwargs["seed"]))
        return [project / "outputs" / f"{name}_00001_.png"]

    rendered = render_project.render_project(
        project, make_panel_fn=fake_make_panel, log=lambda _message: None
    )

    assert rendered == ["panel-01", "panel-02"]
    assert [call[0] for call in calls] == ["panel-01", "panel-02"]
    metadata = json.loads((project / "requests" / "panel-01-production.json").read_text(encoding="utf-8"))
    assert metadata["seed"] == 1
    assert metadata["references"][0]["id"] == "maya"


def test_stops_on_first_failure_and_writes_failure_sidecar(tmp_path):
    project = _make_project(tmp_path, ["panel-01", "panel-02"])
    calls = []

    def failing_make_panel(project_dir, name, references, **kwargs):
        calls.append(name)
        raise RuntimeError("ComfyUI exploded")

    with pytest.raises(RuntimeError, match="panel-01"):
        render_project.render_project(
            project, make_panel_fn=failing_make_panel, log=lambda _message: None
        )

    failure = json.loads((project / "requests" / "panel-01-failure.json").read_text(encoding="utf-8"))
    assert failure["error"] == "ComfyUI exploded"
    assert calls == ["panel-01"]
    assert not (project / "requests" / "panel-02-production.json").exists()


def test_resume_skips_panel_with_matching_metadata(tmp_path):
    project = _make_project(tmp_path, ["panel-01"])
    calls = []

    def fake_make_panel(project_dir, name, references, **kwargs):
        calls.append(name)
        return [project / "outputs" / f"{name}_00001_.png"]

    render_project.render_project(project, make_panel_fn=fake_make_panel, log=lambda _message: None)
    assert calls == ["panel-01"]

    rendered = render_project.render_project(
        project, resume=True, make_panel_fn=fake_make_panel, log=lambda _message: None
    )

    assert rendered == []
    assert calls == ["panel-01"]


def test_resume_rerenders_when_brief_changed(tmp_path):
    project = _make_project(tmp_path, ["panel-01"])
    calls = []

    def fake_make_panel(project_dir, name, references, **kwargs):
        calls.append(name)
        return [project / "outputs" / f"{name}_00001_.png"]

    render_project.render_project(project, make_panel_fn=fake_make_panel, log=lambda _message: None)
    (project / "briefs" / "panel-01.txt").write_text("A different brief.", encoding="utf-8")

    rendered = render_project.render_project(
        project, resume=True, make_panel_fn=fake_make_panel, log=lambda _message: None
    )

    assert rendered == ["panel-01"]
    assert calls == ["panel-01", "panel-01"]


def test_start_at_and_stop_after_bound_the_run(tmp_path):
    project = _make_project(tmp_path, ["panel-01", "panel-02", "panel-03"])
    calls = []

    def fake_make_panel(project_dir, name, references, **kwargs):
        calls.append(name)
        return [project / "outputs" / f"{name}_00001_.png"]

    rendered = render_project.render_project(
        project,
        start_at="panel-02",
        stop_after="panel-02",
        make_panel_fn=fake_make_panel,
        log=lambda _message: None,
    )

    assert rendered == ["panel-02"]
    assert calls == ["panel-02"]


def test_rejects_unapproved_reference_before_any_render(tmp_path):
    project = _make_project(tmp_path, ["panel-01"])
    catalog = json.loads((project / "references.json").read_text(encoding="utf-8"))
    catalog["references"]["maya"]["status"] = "draft"
    _write(project / "references.json", catalog)
    calls = []

    with pytest.raises(Exception, match="not approved"):
        render_project.render_project(
            project,
            make_panel_fn=lambda *a, **k: calls.append(1) or [],
            log=lambda _message: None,
        )

    assert calls == []
