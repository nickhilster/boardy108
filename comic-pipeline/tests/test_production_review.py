import json

import pytest

from test_batch_runner import project, renderer
import batch_runner
import production_review


@pytest.fixture
def completed(project):
    result = batch_runner.run_batches(project, batch_id="set-01-first-contact", make_panel_fn=renderer([]), log=lambda _: None)
    return project, result["scenes"][0]["name"]


@pytest.mark.parametrize("status", ["accepted", "rejected", "review-needed"])
def test_review_is_hash_bound(completed, status):
    project, name = completed
    record = production_review.record_review(project, name, status, "human note", reviewer="Nick")
    assert record["status"] == status and record["reviewer"] == "Nick"
    assert record["timestamp"].endswith("+00:00")
    output = production_review.current_output(project, name)
    assert output["render_status"] == "complete" and output["review_status"] == status
    with pytest.raises(ValueError, match="Stale"):
        production_review.record_review(project, name, status, expected_output_hash="old")
    from pathlib import Path
    Path(output["outputs"][0]).write_bytes(b"different image")
    with pytest.raises(ValueError, match="Stale"):
        production_review.record_review(project, name, status)
    assert production_review.review_status(project, name, "different hash") == "pending"


def test_bad_review_and_unknown_scene(completed):
    project, name = completed
    with pytest.raises(ValueError, match="Unsupported"):
        production_review.record_review(project, name, "complete")
    with pytest.raises(ValueError, match="Unknown"):
        production_review.record_review(project, "unknown", "accepted")


def test_revision_preserves_original_and_renders_new_brief(completed):
    project, name = completed
    panel = production_review.scene_panel(project, name)
    original = panel["brief"].read_bytes()
    revision = project / "replacement.txt"
    revision.write_text("User supplied replacement brief", encoding="utf-8")
    first = production_review.record_brief_revision(project, name, revision)
    revision.write_text("Another replacement", encoding="utf-8")
    second = production_review.record_brief_revision(project, name, revision)
    assert second["previous_brief_hash"] == first["brief_hash"]
    assert first["scene_id"] == second["scene_id"] == panel["scene_number"]
    assert panel["brief"].read_bytes() == original
    assert (project / first["brief_path"]).read_text() == "User supplied replacement brief"
    calls = []
    batch_runner.run_batches(project, batch_id="set-01-first-contact", resume=True, make_panel_fn=renderer(calls), log=lambda _: None)
    assert [item[0] for item in calls] == [name]
    assert calls[0][1]["brief_path"] == project / second["brief_path"]
    assert production_review.current_output(project, name)["brief_revision"] == 2


def test_review_history_preserved(completed):
    project, name = completed
    production_review.record_review(project, name, "review-needed")
    production_review.record_review(project, name, "accepted")
    history = json.loads((project / "reviews" / f"{name}.json").read_text())
    assert [entry["status"] for entry in history] == ["review-needed", "accepted"]


@pytest.mark.parametrize("status", ["rejected", "review-needed"])
def test_review_status_does_not_trigger_reroll(completed, status):
    project, name = completed
    production_review.record_review(project, name, status)
    calls = []
    run = batch_runner.run_batches(project, batch_id="set-01-first-contact", resume=True, make_panel_fn=renderer(calls), log=lambda _: None)
    assert calls == []
    assert run["scenes"][0]["review_status"] == status
    assert run["render_status"] == "complete" and run["review_status"] == status


def test_review_cli_rejects_stale_and_unknown(completed, capsys):
    import review_project
    project, name = completed
    args = ["--project", str(project), "--scene", name, "--status", "accepted"]
    assert review_project.main(args + ["--output-hash", "stale"]) == 1
    assert "Stale" in capsys.readouterr().err
    assert review_project.main(args) == 0
    capsys.readouterr()
    args[3] = "unknown"
    assert review_project.main(args) == 1
    assert "Unknown scene" in capsys.readouterr().err


def test_changed_image_invalidates_old_decision(completed):
    from pathlib import Path
    project, name = completed
    production_review.record_review(project, name, "accepted")
    metadata = production_review.current_output(project, name)
    Path(metadata["outputs"][0]).write_bytes(b"new image")
    with pytest.raises(ValueError):
        production_review.record_review(project, name, "accepted")
    assert json.loads((project / "requests" / f"{name}-production.json").read_text())["review_status"] == "pending"
    assert json.loads((project / "reviews" / f"{name}.json").read_text())[0]["status"] == "accepted"


def test_new_attempt_preserves_original_image_and_review(completed):
    project, name = completed
    production_review.record_review(project, name, "accepted")
    old = production_review.current_output(project, name)
    good = renderer([])
    def changed(project, name, refs, **kwargs):
        outputs = good(project, name, refs, **kwargs)
        outputs[0].write_bytes(b"new result")
        return outputs
    result = batch_runner.run_batches(project, batch_id="set-01-first-contact", resume=True,
                                     model="user-changed-model", make_panel_fn=changed, log=lambda _: None)
    current = result["scenes"][0]["metadata"]
    assert current["attempt"] == 2 and current["review_status"] == "pending"
    assert current["output_hash"] != old["output_hash"]
    archived = project / "attempts" / name / current["attempt_id"] / "previous" / f"{name}.png"
    assert archived.read_bytes() == b"test image"
    assert json.loads((project / "reviews" / f"{name}.json").read_text())[0]["output_hash"] == old["output_hash"]


def test_revision_integrity_blocks_before_run(completed):
    project, name = completed
    replacement = project / "replacement.txt"
    replacement.write_text("replacement brief")
    revision = production_review.record_brief_revision(project, name, replacement)
    (project / revision["brief_path"]).write_text("silent edit")
    before = set((project / "runs").iterdir())
    with pytest.raises(ValueError, match="changed"):
        batch_runner.run_batches(project, resume=True, make_panel_fn=renderer([]), log=lambda _: None)
    assert set((project / "runs").iterdir()) == before
