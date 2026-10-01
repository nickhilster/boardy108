import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import make_panel


VALID_PANEL = {
    "camera": "medium-wide low angle",
    "composition": "hero on the left, doorway on the right",
    "characters": ["Mara", "Courier"],
    "locked_traits": ["Mara has a silver left-eye implant"],
    "lighting": "cyan window light and a warm desk lamp",
    "continuity": "the broken cup remains beside the ledger",
    "positive_prompt": "comic panel, medium-wide low angle",
    "negative_prompt": "costume changes, text, speech bubbles",
}


class OllamaStub:
    def __init__(self, chat_content: str):
        self.chat_content = chat_content
        self.requests = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers["Content-Length"])
                body = json.loads(self.rfile.read(length))
                owner.requests.append((self.path, body))
                if self.path == "/api/chat":
                    response = {"message": {"role": "assistant", "content": owner.chat_content}, "done": True}
                elif self.path == "/api/generate":
                    response = {"done": True}
                else:
                    self.send_error(404)
                    return
                encoded = json.dumps(response).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, *_args):
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self):
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


class ComfyStub:
    def __init__(
        self,
        image_filename: str = "panel_00001_.png",
        image_subfolder: str = "test-project",
        fail_system_stats_times: int = 0,
    ):
        self.requests = []
        self.image_filename = image_filename
        self.image_subfolder = image_subfolder
        self.fail_system_stats_times = fail_system_stats_times
        self._system_stats_calls = 0
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                owner.requests.append(("GET", self.path, None))
                if self.path == "/system_stats":
                    owner._system_stats_calls += 1
                    if owner._system_stats_calls <= owner.fail_system_stats_times:
                        self.send_error(500, "sticky CUDA error (simulated)")
                        return
                    self._json({"system": {"comfyui_version": "0.36.0"}, "devices": []})
                elif self.path == "/object_info":
                    self._json({"UNETLoader": {}, "CLIPLoader": {}, "VAELoader": {}})
                elif self.path.startswith("/history/"):
                    prompt_id = self.path.rsplit("/", 1)[-1]
                    self._json(
                        {
                            prompt_id: {
                                "status": {"status_str": "success", "completed": True},
                                "outputs": {
                                    "19": {
                                        "images": [
                                            {
                                                "filename": owner.image_filename,
                                                "subfolder": owner.image_subfolder,
                                                "type": "output",
                                            }
                                        ]
                                    }
                                },
                            }
                        }
                    )
                else:
                    self.send_error(404)

            def do_POST(self):
                length = int(self.headers["Content-Length"])
                raw = self.rfile.read(length)
                owner.requests.append(("POST", self.path, raw))
                if self.path == "/prompt":
                    self._json({"prompt_id": "prompt-123", "number": 1, "node_errors": {}})
                elif self.path == "/upload/image":
                    self._json({"name": "reference.jpg", "subfolder": owner.image_subfolder, "type": "input"})
                else:
                    self.send_error(404)

            def _json(self, value):
                encoded = json.dumps(value).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, *_args):
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self):
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def _make_project(tmp_path: Path, name: str) -> Path:
    project = tmp_path / "test-project"
    (project / "briefs").mkdir(parents=True)
    (project / "references").mkdir()
    (project / "requests").mkdir()
    (project / "briefs" / f"{name}.txt").write_text("Mara confronts the courier.", encoding="utf-8")
    return project


def test_make_panel_runs_direction_then_render_and_saves_output(tmp_path):
    project = _make_project(tmp_path, "panel-01")
    reference = project / "references" / "mara.jpg"
    reference.write_bytes(b"jpeg-data")

    comfy_output_root = tmp_path / "comfy-output"
    rendered_subfolder = comfy_output_root / "test-project"
    rendered_subfolder.mkdir(parents=True)
    (rendered_subfolder / "panel_00001_.png").write_bytes(b"fake-png-bytes")

    with OllamaStub(json.dumps(VALID_PANEL)) as ollama, ComfyStub(image_subfolder="test-project") as comfy:
        saved_paths = make_panel.make_panel(
            project,
            "panel-01",
            [reference],
            seed=7,
            ollama_url=ollama.base_url,
            comfy_url=comfy.base_url,
            comfy_output_root=comfy_output_root,
            render_timeout=5,
            log=lambda _message: None,
        )

    assert len(saved_paths) == 1
    assert saved_paths[0] == project / "outputs" / "panel_00001_.png"
    assert saved_paths[0].read_bytes() == b"fake-png-bytes"
    assert json.loads((project / "requests" / "panel-01.json").read_text(encoding="utf-8")) == VALID_PANEL
    assert (project / "requests" / "panel-01-workflow.json").exists()


def test_make_panel_raises_when_comfyui_is_unreachable(tmp_path):
    project = _make_project(tmp_path, "panel-01")
    reference = project / "references" / "mara.jpg"
    reference.write_bytes(b"jpeg-data")

    with OllamaStub(json.dumps(VALID_PANEL)) as ollama:
        with pytest.raises(RuntimeError, match="Start ComfyUI first"):
            make_panel.make_panel(
                project,
                "panel-01",
                [reference],
                seed=7,
                ollama_url=ollama.base_url,
                comfy_url="http://127.0.0.1:1",
                render_timeout=5,
                log=lambda _message: None,
            )

    assert json.loads((project / "requests" / "panel-01.json").read_text(encoding="utf-8")) == VALID_PANEL


def test_make_panel_recovers_after_one_transient_comfy_failure(tmp_path):
    project = _make_project(tmp_path, "panel-01")
    reference = project / "references" / "mara.jpg"
    reference.write_bytes(b"jpeg-data")

    comfy_output_root = tmp_path / "comfy-output"
    rendered_subfolder = comfy_output_root / "test-project"
    rendered_subfolder.mkdir(parents=True)
    (rendered_subfolder / "panel_00001_.png").write_bytes(b"fake-png-bytes")

    recover_calls = []

    with OllamaStub(json.dumps(VALID_PANEL)) as ollama, ComfyStub(
        image_subfolder="test-project", fail_system_stats_times=1
    ) as comfy:
        saved_paths = make_panel.make_panel(
            project,
            "panel-01",
            [reference],
            seed=7,
            ollama_url=ollama.base_url,
            comfy_url=comfy.base_url,
            comfy_output_root=comfy_output_root,
            render_timeout=5,
            recover=lambda: recover_calls.append(1),
            log=lambda _message: None,
        )

    assert len(recover_calls) == 1
    assert len(saved_paths) == 1
    assert saved_paths[0].read_bytes() == b"fake-png-bytes"


def test_make_panel_raises_when_retry_after_recovery_still_fails(tmp_path):
    project = _make_project(tmp_path, "panel-01")
    reference = project / "references" / "mara.jpg"
    reference.write_bytes(b"jpeg-data")

    with OllamaStub(json.dumps(VALID_PANEL)) as ollama, ComfyStub(
        image_subfolder="test-project", fail_system_stats_times=99
    ) as comfy:
        with pytest.raises(RuntimeError, match="Start ComfyUI first"):
            make_panel.make_panel(
                project,
                "panel-01",
                [reference],
                seed=7,
                ollama_url=ollama.base_url,
                comfy_url=comfy.base_url,
                render_timeout=5,
                recover=lambda: None,
                log=lambda _message: None,
            )


def test_make_panel_reports_both_errors_when_recovery_itself_fails(tmp_path):
    project = _make_project(tmp_path, "panel-01")
    reference = project / "references" / "mara.jpg"
    reference.write_bytes(b"jpeg-data")

    def _broken_recovery():
        raise RuntimeError("ComfyUI did not become healthy after restart")

    with OllamaStub(json.dumps(VALID_PANEL)) as ollama:
        with pytest.raises(RuntimeError, match="automatic recovery also failed"):
            make_panel.make_panel(
                project,
                "panel-01",
                [reference],
                seed=7,
                ollama_url=ollama.base_url,
                comfy_url="http://127.0.0.1:1",
                render_timeout=5,
                recover=_broken_recovery,
                log=lambda _message: None,
            )


def test_make_panel_does_not_recover_by_default(tmp_path):
    project = _make_project(tmp_path, "panel-01")
    reference = project / "references" / "mara.jpg"
    reference.write_bytes(b"jpeg-data")

    with OllamaStub(json.dumps(VALID_PANEL)) as ollama, ComfyStub(
        image_subfolder="test-project", fail_system_stats_times=1
    ) as comfy:
        with pytest.raises(RuntimeError, match="Start ComfyUI first"):
            make_panel.make_panel(
                project,
                "panel-01",
                [reference],
                seed=7,
                ollama_url=ollama.base_url,
                comfy_url=comfy.base_url,
                render_timeout=5,
                log=lambda _message: None,
            )


def test_parse_args_no_auto_recover_flag_defaults_to_recovery_enabled():
    args = make_panel.parse_args(
        ["--project", "p", "--name", "n", "--reference", "r.jpg", "--seed", "1"]
    )
    assert args.no_auto_recover is False

    args = make_panel.parse_args(
        ["--project", "p", "--name", "n", "--reference", "r.jpg", "--seed", "1", "--no-auto-recover"]
    )
    assert args.no_auto_recover is True


def test_parse_args_director_defaults_to_local():
    args = make_panel.parse_args(["--project", "p", "--name", "n", "--reference", "r.jpg", "--seed", "1"])
    assert args.director == "local"

    args = make_panel.parse_args(
        ["--project", "p", "--name", "n", "--reference", "r.jpg", "--seed", "1", "--director", "cloud"]
    )
    assert args.director == "cloud"


def test_make_panel_director_cloud_calls_the_injected_direct_fn_not_ollama(tmp_path):
    project = _make_project(tmp_path, "panel-01")
    reference = project / "references" / "mara.jpg"
    reference.write_bytes(b"jpeg-data")

    comfy_output_root = tmp_path / "comfy-output"
    rendered_subfolder = comfy_output_root / "test-project"
    rendered_subfolder.mkdir(parents=True)
    (rendered_subfolder / "panel_00001_.png").write_bytes(b"fake-png-bytes")

    calls = []

    def fake_direct_fn(brief, references, panel_json, **kwargs):
        calls.append(kwargs)
        panel_json.parent.mkdir(parents=True, exist_ok=True)
        panel_json.write_text(json.dumps(VALID_PANEL), encoding="utf-8")
        return dict(VALID_PANEL)

    with ComfyStub(image_subfolder="test-project") as comfy:
        make_panel.make_panel(
            project,
            "panel-01",
            [reference],
            seed=7,
            director="cloud",
            direct_fn=fake_direct_fn,
            comfy_url=comfy.base_url,
            comfy_output_root=comfy_output_root,
            render_timeout=5,
            log=lambda _message: None,
        )

    assert len(calls) == 1
    assert calls[0]["provider"] is not None
    assert calls[0]["model"] is not None
