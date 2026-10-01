"""Record observed announcement receipts. No caller-supplied event times."""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import production_review


def record_announcement(project_dir: Path | str, run_id: str, event: str,
                        *, correction_of: str | None = None) -> dict[str, Any]:
    received_at = production_review.utc_now()
    if event not in {"start", "finish"}:
        raise ValueError("Announcement event must be start or finish")
    if not run_id or any(character not in "0123456789abcdef" for character in run_id) or len(run_id) != 32:
        raise ValueError(f"Unknown run ID: {run_id}")
    project = Path(project_dir).resolve()
    run_path = project / "runs" / f"{run_id}.json"
    if not run_path.is_file() or json.loads(run_path.read_text(encoding="utf-8")).get("run_id") != run_id:
        raise ValueError(f"Unknown run ID: {run_id}")
    path = project / "runs" / f"{run_id}-events.json"
    history = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    if correction_of is not None:
        if not any(item["event_id"] == correction_of and item["event"] == event for item in history):
            raise ValueError("Correction must identify a previous receipt of this event")
    elif any(item["event"] == event for item in history):
        raise ValueError(f"Announcement {event} already recorded; use an explicit correction")
    record = {"event_id": uuid.uuid4().hex, "run_id": run_id, "event": event,
              "received_at": received_at, "correction_of": correction_of}
    production_review.write_atomic(path, history + [record])
    return record
