import json
from datetime import datetime, timezone

import pytest

from test_batch_runner import project, renderer
import batch_runner
import production_events


def test_receipt_is_current_and_append_only(project):
    run = batch_runner.run_batches(project, batch_id="set-01-first-contact", make_panel_fn=renderer([]), log=lambda _: None)
    before = datetime.now(timezone.utc)
    first = production_events.record_announcement(project, run["run_id"], "start")
    after = datetime.now(timezone.utc)
    assert before <= datetime.fromisoformat(first["received_at"]) <= after
    with pytest.raises(ValueError, match="already recorded"):
        production_events.record_announcement(project, run["run_id"], "start")
    correction = production_events.record_announcement(project, run["run_id"], "start", correction_of=first["event_id"])
    assert correction["event_id"] != first["event_id"]
    finish = production_events.record_announcement(project, run["run_id"], "finish")
    records = json.loads((project / "runs" / f"{run['run_id']}-events.json").read_text())
    assert records == [first, correction, finish]
    with pytest.raises(TypeError):
        production_events.record_announcement(project, run["run_id"], "start", timestamp="2000-01-01")


def test_invalid_receipts(project):
    with pytest.raises(ValueError, match="Unknown run"):
        production_events.record_announcement(project, "unknown", "start")
    with pytest.raises(ValueError, match="start or finish"):
        production_events.record_announcement(project, "unknown", "past")
    with pytest.raises(ValueError):
        production_events.record_announcement(project, "../production", "start")


def test_receipt_cli_has_no_timestamp_override(project):
    import run_event
    with pytest.raises(SystemExit) as error:
        run_event.main(["--project", str(project), "--run-id", "unknown", "--event", "start", "--timestamp", "2000-01-01"])
    assert error.value.code == 2
    assert run_event.main(["--project", str(project), "--run-id", "unknown", "--event", "start"]) == 1
