import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import make_panel


@pytest.mark.parametrize("director", ["local", "cloud"])
def test_boardy_output_and_local_canon(tmp_path, monkeypatch, director):
    project = tmp_path / "project"
    (project / "briefs").mkdir(parents=True)
    (project / "briefs" / "boardy-001.txt").write_text("scene")
    source = tmp_path / "comfy" / "render.png"
    source.parent.mkdir()
    source.write_bytes(b"image")
    calls = []
    def direct(*args, **kwargs):
        calls.append(kwargs)
    client = make_panel.comfy_client
    monkeypatch.setattr(client, "health", lambda *a, **k: None)
    monkeypatch.setattr(client, "upload_image", lambda *a, **k: "reference.png")
    monkeypatch.setattr(client, "prepare", lambda *a, **k: {})
    monkeypatch.setattr(client, "submit", lambda *a, **k: "prompt")
    monkeypatch.setattr(client, "wait_for_completion", lambda *a, **k: {})
    monkeypatch.setattr(client, "output_images", lambda *a, **k: [{"filename": "render.png"}])
    output = make_panel.make_panel(project, "boardy-001", [], seed=1, director=director,
                                  direct_fn=direct, comfy_output_root=source.parent,
                                  canon_instructions="canon", output_subdir="set-01-first-contact", log=lambda _: None)
    assert output == [project / "outputs" / "set-01-first-contact" / "boardy-001.png"]
    assert output[0].read_bytes() == b"image"
    assert calls[0].get("canon_instructions") == "canon"


@pytest.mark.parametrize("folder", ["../outside", "nested/folder"])
def test_output_subdir_rejected_before_direction(tmp_path, folder):
    with pytest.raises(ValueError, match="output_subdir"):
        make_panel.make_panel(tmp_path, "scene", [], seed=1, output_subdir=folder,
                              direct_fn=lambda *a, **k: pytest.fail("must not direct"))
