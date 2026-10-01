import hashlib
import json
import shutil
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import batch_runner


def test_cli_options():
    args = batch_runner.parse_args(["--project", "comic-pipeline/projects/boardy-108", "--dry-run",
                                   "--batch", "set-01-first-contact", "--resume"])
    assert args.project == Path("comic-pipeline/projects/boardy-108")
    assert args.dry_run and args.resume and args.batch == "set-01-first-contact"


def test_cli_cloud_director_options_propagate(project, monkeypatch):
    seen = {}
    monkeypatch.setattr(batch_runner, "run_batches",
                        lambda *args, **kwargs: seen.update(kwargs) or {"status": "complete", "failures": []})
    assert batch_runner.main(["--project", str(project), "--director", "cloud",
                              "--cloud-provider", "anthropic", "--cloud-model", "model-x"]) == 0
    assert (seen["director"], seen["cloud_provider"], seen["cloud_model"]) == ("cloud", "anthropic", "model-x")
    assert batch_runner.parse_args(["--project", "p"]).director == "local"


def test_cli_dry_run_json_and_progress(project, capsys):
    assert batch_runner.main(["--project", str(project), "--dry-run"]) == 0
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert len(report["batches"]) == 9 and len(report["scenes"]) == 108
    assert "Run ID:" in captured.err
    assert not (project / "runs").exists()


def test_cli_invalid_batch_and_failed_summary(project, capsys, monkeypatch):
    assert batch_runner.main(["--project", str(project), "--batch", "wrong"]) == 1
    assert "Batch not found" in capsys.readouterr().err
    assert not (project / "runs").exists()
    monkeypatch.setattr(batch_runner, "run_batches", lambda *args, **kwargs: {"status": "partial", "failures": [{}]})
    assert batch_runner.main(["--project", str(project)]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "partial"


def test_single_scene_smoke_selection(project):
    manifest = json.loads((project / "production.json").read_text())
    name = manifest["panels"][0]["name"]
    calls = []
    result = batch_runner.run_batches(project, scene_name=name, make_panel_fn=renderer(calls), log=lambda _: None)
    assert len(calls) == len(result["scenes"]) == len(result["batches"]) == 1
    assert result["scenes"][0]["name"] == name
    with pytest.raises(ValueError, match="does not belong"):
        batch_runner.run_batches(project, scene_name=name, batch_id="set-02-cartography", make_panel_fn=renderer(calls))
    with pytest.raises(ValueError, match="Scene not found"):
        batch_runner.run_batches(project, scene_name="missing", make_panel_fn=renderer(calls))
    assert len(calls) == 1


def test_run_id_is_persisted_before_first_progress(project):
    name = json.loads((project / "production.json").read_text())["panels"][0]["name"]
    messages = []
    def log(message):
        if not messages:
            assert message.startswith("Run ID: ")
            run_id = message.removeprefix("Run ID: ")
            saved = json.loads((project / "runs" / f"{run_id}.json").read_text())
            assert saved["run_id"] == run_id
            assert saved["scenes"][0]["render_status"] == "pending"
        messages.append(message)
    batch_runner.run_batches(project, scene_name=name, make_panel_fn=renderer([]), log=log)


def test_cli_scene_selection(project, capsys):
    name = json.loads((project / "production.json").read_text())["panels"][0]["name"]
    assert batch_runner.main(["--project", str(project), "--scene", name, "--dry-run"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert [scene["name"] for scene in result["scenes"]] == [name]


def test_preflight_cli_nine_batch_counts(project, capsys):
    assert batch_runner.preflight_project.main(["--project", str(project)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert len(report["batches"]) == 9
    assert all(batch["expected_count"] == len(batch["scenes"]) == 12 for batch in report["batches"])


@pytest.fixture
def project(tmp_path):
    source = Path(__file__).resolve().parents[1] / "projects" / "boardy-108"
    project = tmp_path / "boardy"
    shutil.copytree(source, project, ignore=shutil.ignore_patterns("runs", "attempts", "reviews", "outputs", "requests"))
    image = project / "references" / "boardy-canonical.png"
    image.parent.mkdir(exist_ok=True)
    Image.new("RGB", (2, 2)).save(image)
    catalog_path = project / "references.json"
    catalog = json.loads(catalog_path.read_text())
    catalog["references"]["boardy"].update(status="approved", sha256=hashlib.sha256(image.read_bytes()).hexdigest())
    catalog_path.write_text(json.dumps(catalog))
    return project


def renderer(calls):
    def render(project, name, references, **kwargs):
        calls.append((name, kwargs))
        output = project / "outputs" / kwargs["output_subdir"] / f"{name}.png"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"test image")
        return [output]
    return render


def test_dry_run_and_order(project):
    calls = []
    result = batch_runner.run_batches(project, dry_run=True, make_panel_fn=renderer(calls), log=lambda _: None)
    assert calls == []
    assert len(result["scenes"]) == 108
    result = batch_runner.run_batches(project, make_panel_fn=renderer(calls), log=lambda _: None)
    assert result["status"] == "complete"
    assert [scene["name"] for scene in result["scenes"]] == [name for name, _ in calls]
    assert [scene["scene_number"] for scene in result["scenes"]] == list(range(1, 109))
    assert all("BOARDY CANON" in kwargs["canon_instructions"] for _, kwargs in calls)
    assert len(result["batches"]) == 9


def test_batch_selection_and_failure_isolation(project):
    calls = []
    good = renderer(calls)
    def sometimes_fails(project, name, references, **kwargs):
        if name.startswith("boardy-002-"):
            calls.append((name, kwargs))
            raise OSError("disk failure")
        return good(project, name, references, **kwargs)
    result = batch_runner.run_batches(project, make_panel_fn=sometimes_fails, log=lambda _: None)
    assert len(calls) == 108
    assert result["status"] == "partial"
    assert result["scenes"][1]["status"] == "failed"
    assert result["scenes"][12]["status"] == "complete"
    calls.clear()
    result = batch_runner.run_batches(project, batch_id="set-02-cartography", make_panel_fn=good, log=lambda _: None)
    assert len(calls) == 12
    assert len(result["batches"]) == 1


@pytest.mark.parametrize("kind", ["zero", "multiple", "missing", "one"])
def test_one_existing_image_only(project, kind):
    def fake(project, name, references, **kwargs):
        path = project / "outputs" / kwargs["output_subdir"] / f"{name}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        if kind != "missing":
            path.write_bytes(b"image")
        return [] if kind == "zero" else [path, path] if kind == "multiple" else [path]
    result = batch_runner.run_batches(project, batch_id="set-01-first-contact", make_panel_fn=fake, log=lambda _: None)
    name = result["scenes"][0]["name"]
    assert (project / "requests" / f"{name}-production.json").is_file() == (kind == "one")
    assert (project / "requests" / f"{name}-failure.json").is_file() == (kind != "one")


def test_preflight_blocks_before_render(project):
    (project / "references" / "boardy-canonical.png").unlink()
    calls = []
    with pytest.raises(ValueError, match="preflight"):
        batch_runner.run_batches(project, make_panel_fn=renderer(calls))
    assert calls == []


def test_wrong_scene_output_fails_and_removes_stale_success(project):
    calls = []
    good = renderer(calls)
    batch_runner.run_batches(project, batch_id="set-01-first-contact", make_panel_fn=good, log=lambda _: None)
    def wrong_scene(project, name, references, **kwargs):
        output = project / "outputs" / kwargs["output_subdir"] / "wrong-scene.png"
        output.write_bytes(b"image")
        return [output]
    result = batch_runner.run_batches(project, batch_id="set-01-first-contact", make_panel_fn=wrong_scene, log=lambda _: None)
    assert result["status"] == "failed"
    for scene in result["scenes"]:
        assert not (project / "requests" / f"{scene['name']}-production.json").exists()


def test_unknown_batch_rejected_without_render(project):
    calls = []
    with pytest.raises(ValueError, match="Batch not found"):
        batch_runner.run_batches(project, batch_id="wrong", make_panel_fn=renderer(calls))
    assert calls == []


@pytest.mark.parametrize("setting", ["seed", "width", "height"])
def test_manifest_generation_settings_cannot_be_overridden(project, setting):
    calls = []
    result = batch_runner.run_batches(project, batch_id="set-01-first-contact",
                                     make_panel_fn=renderer(calls), log=lambda _: None,
                                     **{setting: 17})
    assert calls == []
    assert result["status"] == "failed"
    assert all("must match the production manifest" in scene["error"] for scene in result["scenes"])
    assert not list((project / "requests").glob("*-production.json"))


def test_equal_manifest_dimension_options_are_allowed(project):
    calls = []
    result = batch_runner.run_batches(project, batch_id="set-01-first-contact",
                                     make_panel_fn=renderer(calls), width=1024, height=1024,
                                     log=lambda _: None)
    assert len(calls) == 12
    assert result["status"] == "complete"


def test_run_provenance_and_resume(project):
    calls = []
    result = batch_runner.run_batches(project, batch_id="set-01-first-contact", make_panel_fn=renderer(calls), model="fake", log=lambda _: None)
    saved = json.loads((project / "runs" / f"{result['run_id']}.json").read_text())
    assert saved["started_at"].endswith("+00:00") and saved["finished_at"].endswith("+00:00")
    assert saved["elapsed_seconds"] >= 0 and saved["comparison_seconds"] == 355
    assert saved["render_status"] == "complete" and saved["review_status"] == "pending"
    metadata = saved["scenes"][0]["metadata"]
    assert all(metadata[key] for key in ("brief_hash", "canon_hash", "model_hash", "render_hash", "input_hash", "output_hash"))
    calls.clear()
    batch_runner.run_batches(project, batch_id="set-01-first-contact", resume=True, make_panel_fn=renderer(calls), model="fake", log=lambda _: None)
    assert calls == []
    batch_runner.run_batches(project, batch_id="set-01-first-contact", resume=True, make_panel_fn=renderer(calls), model="changed", log=lambda _: None)
    assert len(calls) == 12
    assert list((project / "attempts" / metadata["name"]).rglob("*.png"))


@pytest.mark.parametrize("change", ["brief", "canon", "reference", "seed", "dimension", "settings", "output"])
def test_every_input_and_output_invalidates_resume(project, change):
    calls = []
    run = batch_runner.run_batches(project, batch_id="set-01-first-contact", make_panel_fn=renderer(calls), log=lambda _: None)
    first = run["scenes"][0]["metadata"]
    panel = json.loads((project / "production.json").read_text())["panels"][0]
    options = {}
    if change == "brief":
        path = project / panel["brief"]
        path.write_bytes(path.read_bytes() + b"\r\n")
    elif change == "canon":
        path = project / "canon.json"
        path.write_bytes(path.read_bytes() + b"\n")
    elif change == "reference":
        path = project / "references" / "boardy-canonical.png"
        Image.new("RGB", (2, 2), "red").save(path)
        catalog = json.loads((project / "references.json").read_text())
        catalog["references"]["boardy"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        (project / "references.json").write_text(json.dumps(catalog))
    elif change in {"seed", "dimension"}:
        path = project / "production.json"
        manifest = json.loads(path.read_text())
        manifest["panels"][0]["seed" if change == "seed" else "width"] += 32
        path.write_text(json.dumps(manifest))
    elif change == "settings":
        options["max_render_references"] = 2
    else:
        Path(first["outputs"][0]).write_bytes(b"modified image")
    calls.clear()
    batch_runner.run_batches(project, batch_id="set-01-first-contact", resume=True, make_panel_fn=renderer(calls), log=lambda _: None, **options)
    assert len(calls) == (12 if change in {"canon", "reference", "settings"} else 1)


def test_failure_attempts_and_run_checkpoint(project):
    def fail(project, name, refs, **kwargs):
        raise RuntimeError("deterministic failure")
    result = batch_runner.run_batches(project, batch_id="set-01-first-contact", make_panel_fn=fail, log=lambda _: None)
    assert result["render_status"] == "failed" and len(result["failures"]) == 12
    assert result["finished_at"] and result["elapsed_seconds"] >= 0
    assert all(scene["failure"]["attempt"] == 1 for scene in result["scenes"])
    assert all(scene["failure"]["brief_hash"] for scene in result["scenes"])


def test_rejected_options_preserve_prior_provenance(project):
    first = batch_runner.run_batches(project, batch_id="set-01-first-contact", make_panel_fn=renderer([]), log=lambda _: None)
    name = first["scenes"][0]["name"]
    metadata_path = project / "requests" / f"{name}-production.json"
    original = metadata_path.read_bytes()
    calls = []
    result = batch_runner.run_batches(project, batch_id="set-01-first-contact", seed=17, make_panel_fn=renderer(calls), log=lambda _: None)
    assert not calls and result["status"] == "failed"
    assert metadata_path.read_bytes() == original


def test_failed_attempt_archives_unexpected_artifacts(project):
    def fails_after_writing(project, name, refs, **kwargs):
        request = project / "requests" / f"{name}.json"
        request.parent.mkdir(exist_ok=True)
        request.write_bytes(b"failed request")
        (request.parent / f"{name}-workflow.json").write_bytes(b"failed workflow")
        output = project / "outputs" / kwargs["output_subdir"] / "unexpected.png"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(name.encode())
        raise RuntimeError("after writing artifacts")
    result = batch_runner.run_batches(project, batch_id="set-01-first-contact", make_panel_fn=fails_after_writing, log=lambda _: None)
    for scene in result["scenes"]:
        artifacts = scene["failure"]["artifacts"]
        assert len(artifacts) == 3
        unexpected = next(item for item in artifacts if item["source_path"].endswith("unexpected.png"))
        archived = project / unexpected["archive_path"]
        assert archived.read_bytes() == scene["name"].encode()
        assert hashlib.sha256(archived.read_bytes()).hexdigest() == unexpected["hash"]


def test_missing_brief_during_resume_is_isolated(project):
    first = batch_runner.run_batches(project, batch_id="set-01-first-contact", make_panel_fn=renderer([]), log=lambda _: None)
    second = first["scenes"][1]["name"]
    manifest = json.loads((project / "production.json").read_text())
    brief = project / next(panel["brief"] for panel in manifest["panels"] if panel["name"] == second)
    def log(message):
        if message == f"[skip] {first['scenes'][0]['name']}":
            brief.unlink()
    result = batch_runner.run_batches(project, batch_id="set-01-first-contact", resume=True, make_panel_fn=renderer([]), log=log)
    assert result["scenes"][1]["status"] == "failed"
    assert result["scenes"][2]["status"] == "skipped"
    saved = json.loads((project / "runs" / f"{result['run_id']}.json").read_text())
    assert saved["finished_at"] and saved["render_status"] == "partial"
    assert saved["scenes"][1]["failure"]["stage"] == "resume"


def test_open_run_finalized_on_unexpected_termination(project):
    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt("operator interrupt")
    with pytest.raises(KeyboardInterrupt):
        batch_runner.run_batches(project, batch_id="set-01-first-contact", make_panel_fn=interrupt, log=lambda _: None)
    saved = json.loads(next((project / "runs").glob("*.json")).read_text())
    assert saved["finished_at"] and saved["render_status"] == "failed"
    assert saved["failures"] and all(scene["render_status"] != "running" for scene in saved["scenes"])
