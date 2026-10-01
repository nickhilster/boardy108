import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import comfy_client


PANEL = {
    "camera": "low-angle medium shot",
    "composition": "Mara left, open doorway right",
    "characters": ["Mara"],
    "locked_traits": ["silver left-eye implant", "red jacket with black cuffs"],
    "lighting": "cyan moonlight and warm practical light",
    "continuity": "broken cup remains by the ledger",
    "positive_prompt": "inked science-fiction comic panel",
    "negative_prompt": "text, speech bubbles, costume drift",
}


class ComfyStub:
    def __init__(self, history_responses: list[dict] | None = None):
        self.requests = []
        self.history_responses = history_responses if history_responses is not None else []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                owner.requests.append(("GET", self.path, None))
                if self.path == "/system_stats":
                    self._json({"system": {"comfyui_version": "0.36.0"}, "devices": []})
                elif self.path == "/object_info":
                    self._json({"UNETLoader": {}, "CLIPLoader": {}, "VAELoader": {}})
                elif self.path.startswith("/history/"):
                    history_calls = sum(1 for _m, path, _b in owner.requests if path == self.path)
                    if owner.history_responses:
                        index = min(history_calls - 1, len(owner.history_responses) - 1)
                        self._json(owner.history_responses[index])
                    else:
                        self._json({})
                else:
                    self.send_error(404)

            def do_POST(self):
                length = int(self.headers["Content-Length"])
                raw = self.rfile.read(length)
                if self.path == "/prompt":
                    body = json.loads(raw)
                    owner.requests.append(("POST", self.path, body))
                    self._json({"prompt_id": "prompt-123", "number": 1, "node_errors": {}})
                elif self.path == "/upload/image":
                    owner.requests.append(("POST", self.path, raw))
                    self._json({"name": "mara.png", "subfolder": "comic-pipeline", "type": "input"})
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


def test_build_flux_edit_workflow_wires_reference_and_locked_traits():
    workflow = comfy_client.build_flux_edit_workflow(
        PANEL,
        "comic-pipeline/mara.png",
        seed=42,
        filename_prefix="comic/panel-001",
    )

    assert workflow["1"] == {
        "class_type": "UNETLoader",
        "inputs": {"unet_name": "flux-2-klein-4b-fp8.safetensors", "weight_dtype": "default"},
    }
    assert workflow["4"]["inputs"]["image"] == "comic-pipeline/mara.png"
    assert "silver left-eye implant" in workflow["7"]["inputs"]["text"]
    assert workflow["12"]["inputs"]["positive"] == ["10", 0]
    assert workflow["17"]["inputs"]["latent_image"] == ["15", 0]
    assert workflow["19"]["inputs"]["filename_prefix"] == "comic/panel-001"


def test_build_flux_edit_workflow_accepts_string_reference_for_backward_compat():
    workflow = comfy_client.build_flux_edit_workflow(
        PANEL, "comic-pipeline/mara.png", seed=42, filename_prefix="comic/panel-001"
    )
    assert workflow["4"]["inputs"]["image"] == "comic-pipeline/mara.png"
    assert "20" not in workflow


def test_build_flux_edit_workflow_chains_two_references():
    workflow = comfy_client.build_flux_edit_workflow(
        PANEL,
        ["comic-pipeline/mara.png", "comic-pipeline/location.png"],
        seed=42,
        filename_prefix="comic/panel-001",
    )
    # First reference keeps the original fixed node IDs.
    assert workflow["4"]["inputs"]["image"] == "comic-pipeline/mara.png"
    assert workflow["10"]["inputs"] == {"conditioning": ["7", 0], "latent": ["9", 0]}
    assert workflow["11"]["inputs"] == {"conditioning": ["8", 0], "latent": ["9", 0]}
    # Second reference is appended and chained from the first's conditioning.
    assert workflow["20"]["inputs"]["image"] == "comic-pipeline/location.png"
    assert workflow["23"]["class_type"] == "ReferenceLatent"
    assert workflow["23"]["inputs"]["conditioning"] == ["10", 0]
    assert workflow["24"]["inputs"]["conditioning"] == ["11", 0]
    # The guider uses the final chained conditioning, not the first reference's.
    assert workflow["12"]["inputs"]["positive"] == ["23", 0]
    assert workflow["12"]["inputs"]["negative"] == ["24", 0]


def test_build_flux_edit_workflow_chains_three_references():
    workflow = comfy_client.build_flux_edit_workflow(
        PANEL,
        ["a.png", "b.png", "c.png"],
        seed=42,
        filename_prefix="comic/panel-001",
    )
    assert workflow["20"]["inputs"]["image"] == "b.png"
    assert workflow["25"]["inputs"]["image"] == "c.png"
    assert workflow["28"]["inputs"]["conditioning"] == ["23", 0]
    assert workflow["29"]["inputs"]["conditioning"] == ["24", 0]
    assert workflow["12"]["inputs"]["positive"] == ["28", 0]
    assert workflow["12"]["inputs"]["negative"] == ["29", 0]


def test_build_flux_edit_workflow_rejects_too_many_references():
    with pytest.raises(ValueError, match="exceeds the configured maximum"):
        comfy_client.build_flux_edit_workflow(
            PANEL, ["a.png", "b.png", "c.png", "d.png"], seed=42, filename_prefix="panel"
        )


def test_build_flux_edit_workflow_allows_raised_max_render_references():
    workflow = comfy_client.build_flux_edit_workflow(
        PANEL,
        ["a.png", "b.png", "c.png", "d.png"],
        seed=42,
        filename_prefix="panel",
        max_render_references=4,
    )
    assert workflow["30"]["inputs"]["image"] == "d.png"


def test_build_flux_edit_workflow_uses_explicit_dimensions_over_reference_size():
    workflow = comfy_client.build_flux_edit_workflow(
        PANEL, "mara.png", seed=42, filename_prefix="panel", width=1536, height=1024
    )
    assert "6" not in workflow
    assert workflow["14"]["inputs"]["width"] == 1536
    assert workflow["14"]["inputs"]["height"] == 1024
    assert workflow["15"]["inputs"]["width"] == 1536
    assert workflow["15"]["inputs"]["height"] == 1024


def test_build_flux_edit_workflow_defaults_dimensions_from_first_reference():
    workflow = comfy_client.build_flux_edit_workflow(
        PANEL, "mara.png", seed=42, filename_prefix="panel"
    )
    assert workflow["6"] == {"class_type": "GetImageSize", "inputs": {"image": ["5", 0]}}
    assert workflow["14"]["inputs"]["width"] == ["6", 0]
    assert workflow["15"]["inputs"]["height"] == ["6", 1]


def test_validate_api_workflow_rejects_node_without_class_type():
    with pytest.raises(ValueError, match="class_type"):
        comfy_client.validate_api_workflow({"1": {"inputs": {}}})


def test_prepare_writes_reproducible_api_workflow(tmp_path):
    panel_path = tmp_path / "panel.json"
    panel_path.write_text(json.dumps(PANEL), encoding="utf-8")
    output = tmp_path / "workflow-api.json"

    workflow = comfy_client.prepare(
        panel_path,
        "comic-pipeline/mara.png",
        output,
        seed=42,
        filename_prefix="comic/panel-001",
    )

    assert json.loads(output.read_text(encoding="utf-8")) == workflow
    assert workflow["16"]["inputs"]["noise_seed"] == 42


def test_health_checks_both_required_endpoints():
    with ComfyStub() as comfy:
        result = comfy_client.health(comfy.base_url)

    assert result["system_stats"]["system"]["comfyui_version"] == "0.36.0"
    assert set(result["required_loaders"]) == {"UNETLoader", "CLIPLoader", "VAELoader"}
    assert [request[1] for request in comfy.requests] == ["/system_stats", "/object_info"]


def test_submit_returns_prompt_id_and_sends_valid_workflow():
    workflow = comfy_client.build_flux_edit_workflow(PANEL, "mara.png", seed=7, filename_prefix="panel")

    with ComfyStub() as comfy:
        prompt_id = comfy_client.submit(workflow, comfy.base_url, client_id="client-test")

    assert prompt_id == "prompt-123"
    body = comfy.requests[-1][2]
    assert body["client_id"] == "client-test"
    assert body["prompt"] == workflow


def test_upload_image_uses_comfy_input_contract(tmp_path):
    image = tmp_path / "mara.png"
    image.write_bytes(b"png-binary-data")

    with ComfyStub() as comfy:
        name = comfy_client.upload_image(image, comfy.base_url, subfolder="comic-pipeline")

    assert name == "comic-pipeline/mara.png"
    method, path, raw = comfy.requests[-1]
    assert (method, path) == ("POST", "/upload/image")
    assert b'filename="mara.png"' in raw
    assert b'name="subfolder"' in raw
    assert b"comic-pipeline" in raw


COMPLETED_HISTORY_ENTRY = {
    "status": {"status_str": "success", "completed": True},
    "outputs": {"19": {"images": [{"filename": "panel_00001_.png", "subfolder": "comic-pipeline", "type": "output"}]}},
}


def test_wait_for_completion_polls_until_done():
    responses = [{}, {"prompt-123": {"status": {"status_str": "running", "completed": False}}}, {"prompt-123": COMPLETED_HISTORY_ENTRY}]

    with ComfyStub(history_responses=responses) as comfy:
        entry = comfy_client.wait_for_completion("prompt-123", comfy.base_url, timeout=5, poll_interval=0.01)

    assert entry == COMPLETED_HISTORY_ENTRY
    assert comfy_client.output_images(entry) == [
        {"filename": "panel_00001_.png", "subfolder": "comic-pipeline", "type": "output"}
    ]


def test_wait_for_completion_raises_on_error_status():
    error_entry = {"prompt-123": {"status": {"status_str": "error", "completed": False}}}

    with ComfyStub(history_responses=[error_entry]) as comfy:
        with pytest.raises(RuntimeError, match="error"):
            comfy_client.wait_for_completion("prompt-123", comfy.base_url, timeout=5, poll_interval=0.01)


def test_wait_for_completion_times_out_when_never_complete():
    pending_entry = {"prompt-123": {"status": {"status_str": "running", "completed": False}}}

    with ComfyStub(history_responses=[pending_entry]) as comfy:
        with pytest.raises(TimeoutError):
            comfy_client.wait_for_completion("prompt-123", comfy.base_url, timeout=0.05, poll_interval=0.02)
