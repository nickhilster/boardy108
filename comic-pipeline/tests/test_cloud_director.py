import base64
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cloud_director


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


class AnthropicStub:
    """Stands in for api.anthropic.com's /v1/messages, tool-use shaped."""

    def __init__(self, tool_inputs: list[dict] | None = None, omit_tool_use: bool = False):
        self.tool_inputs = tool_inputs if tool_inputs is not None else [dict(VALID_PANEL)]
        self.omit_tool_use = omit_tool_use
        self.requests: list[dict] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers["Content-Length"])
                body = json.loads(self.rfile.read(length))
                owner.requests.append(body)
                call_index = min(len(owner.requests) - 1, len(owner.tool_inputs) - 1)
                if owner.omit_tool_use:
                    response = {"content": [{"type": "text", "text": "no tool call here"}]}
                else:
                    response = {
                        "content": [
                            {
                                "type": "tool_use",
                                "name": "submit_panel_direction",
                                "input": owner.tool_inputs[call_index],
                            }
                        ]
                    }
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


def test_encode_reference_maps_known_suffixes(tmp_path):
    for suffix, expected in ((".jpg", "image/jpeg"), (".jpeg", "image/jpeg"), (".png", "image/png"), (".webp", "image/webp")):
        path = tmp_path / f"ref{suffix}"
        path.write_bytes(b"fake-bytes")
        encoded = cloud_director._encode_reference(path)
        assert encoded["media_type"] == expected
        assert base64.b64decode(encoded["data"]) == b"fake-bytes"


def test_encode_reference_rejects_unknown_suffix(tmp_path):
    path = tmp_path / "ref.gif"
    path.write_bytes(b"data")
    with pytest.raises(ValueError, match="Unsupported"):
        cloud_director._encode_reference(path)


def test_run_direction_with_injected_call_writes_validated_panel(tmp_path):
    brief = tmp_path / "panel.txt"
    brief.write_text("Mara confronts the courier.", encoding="utf-8")
    reference = tmp_path / "mara.jpg"
    reference.write_bytes(b"jpeg-data")
    output = tmp_path / "request.json"

    calls = []

    def fake_call(text, images, schema):
        calls.append((text, images, schema))
        return dict(VALID_PANEL)

    result = cloud_director.run_direction(brief, [reference], output, call=fake_call)

    assert result == VALID_PANEL
    assert json.loads(output.read_text(encoding="utf-8")) == VALID_PANEL
    assert len(calls) == 1
    text, images, schema = calls[0]
    assert "Mara confronts the courier." in text
    assert images[0]["media_type"] == "image/jpeg"
    assert schema["required"] == list(VALID_PANEL)


def test_run_direction_retries_after_a_transient_invalid_response(tmp_path):
    brief = tmp_path / "panel.txt"
    brief.write_text("Mara confronts the courier.", encoding="utf-8")
    reference = tmp_path / "mara.jpg"
    reference.write_bytes(b"jpeg-data")
    output = tmp_path / "request.json"

    incomplete_panel = dict(VALID_PANEL)
    incomplete_panel["composition"] = ""
    responses = iter([incomplete_panel, dict(VALID_PANEL)])

    def flaky_call(_text, _images, _schema):
        return next(responses)

    result = cloud_director.run_direction(brief, [reference], output, call=flaky_call)

    assert result == VALID_PANEL


def test_run_direction_raises_after_exhausting_retries(tmp_path):
    brief = tmp_path / "panel.txt"
    brief.write_text("Mara confronts the courier.", encoding="utf-8")
    reference = tmp_path / "mara.jpg"
    reference.write_bytes(b"jpeg-data")
    output = tmp_path / "request.json"

    def always_invalid(_text, _images, _schema):
        invalid = dict(VALID_PANEL)
        invalid["composition"] = ""
        return invalid

    with pytest.raises(ValueError, match="composition"):
        cloud_director.run_direction(brief, [reference], output, call=always_invalid, max_attempts=2)

    assert not output.exists()


def test_run_direction_raises_when_brief_is_missing(tmp_path):
    reference = tmp_path / "mara.jpg"
    reference.write_bytes(b"jpeg-data")

    with pytest.raises(ValueError, match="does not exist"):
        cloud_director.run_direction(tmp_path / "missing.txt", [reference], tmp_path / "out.json", call=lambda *a: dict(VALID_PANEL))


def test_run_direction_without_call_requires_an_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    brief = tmp_path / "panel.txt"
    brief.write_text("Mara confronts the courier.", encoding="utf-8")
    reference = tmp_path / "mara.jpg"
    reference.write_bytes(b"jpeg-data")

    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        cloud_director.run_direction(brief, [reference], tmp_path / "out.json")


def test_run_direction_rejects_unknown_provider(tmp_path):
    brief = tmp_path / "panel.txt"
    brief.write_text("Mara confronts the courier.", encoding="utf-8")
    reference = tmp_path / "mara.jpg"
    reference.write_bytes(b"jpeg-data")

    with pytest.raises(ValueError, match="Unknown cloud director provider"):
        cloud_director.run_direction(
            brief, [reference], tmp_path / "out.json", provider="not-a-real-provider", api_key="test-key"
        )


def test_anthropic_call_builds_request_and_extracts_tool_input(tmp_path):
    reference = tmp_path / "mara.jpg"
    reference.write_bytes(b"jpeg-data")
    encoded = [cloud_director._encode_reference(reference)]
    schema = {"$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object", "required": list(VALID_PANEL)}

    with AnthropicStub(tool_inputs=[dict(VALID_PANEL)]) as anthropic:
        result = cloud_director._anthropic_call(
            "Mara confronts the courier.",
            encoded,
            schema,
            model="claude-sonnet-5",
            api_key="test-key",
            api_base_url=anthropic.base_url,
        )

    assert result == VALID_PANEL
    sent = anthropic.requests[0]
    assert sent["model"] == "claude-sonnet-5"
    assert sent["tool_choice"] == {"type": "tool", "name": "submit_panel_direction"}
    assert "$schema" not in sent["tools"][0]["input_schema"]
    assert sent["messages"][0]["content"][1]["source"]["media_type"] == "image/jpeg"


def test_canon_instructions_reach_the_anthropic_request(tmp_path, monkeypatch):
    brief = tmp_path / "panel.txt"
    brief.write_text("Boardy with raised eyebrows.", encoding="utf-8")
    reference = tmp_path / "boardy.png"
    reference.write_bytes(b"png-data")
    monkeypatch.setattr(cloud_director, "ANTHROPIC_API_BASE_URL", "unused")

    with AnthropicStub(tool_inputs=[dict(VALID_PANEL)]) as anthropic:
        real_call = cloud_director._anthropic_call
        monkeypatch.setitem(cloud_director.PROVIDERS, "anthropic",
                            lambda *a, **k: real_call(*a, api_base_url=anthropic.base_url, **k))
        cloud_director.run_direction(brief, [reference], tmp_path / "out.json", api_key="test-key",
                                     canon_instructions="NO EYEBROWS CANON")

    text = anthropic.requests[0]["messages"][0]["content"][0]["text"]
    assert "NO EYEBROWS CANON" in text
    assert "canon takes priority" in text
    assert text.index("NO EYEBROWS CANON") < text.index("PANEL BRIEF:")
    assert "Boardy with raised eyebrows." in text


def test_anthropic_call_without_canon_is_unchanged(tmp_path):
    with AnthropicStub(tool_inputs=[dict(VALID_PANEL)]) as anthropic:
        cloud_director._anthropic_call("brief", [], {"type": "object"}, model="m", api_key="k",
                                       api_base_url=anthropic.base_url)
    assert "canon takes priority" not in anthropic.requests[0]["messages"][0]["content"][0]["text"]


def test_anthropic_call_raises_when_no_tool_use_in_response(tmp_path):
    reference = tmp_path / "mara.jpg"
    reference.write_bytes(b"jpeg-data")
    encoded = [cloud_director._encode_reference(reference)]
    schema = {"type": "object", "required": list(VALID_PANEL)}

    with AnthropicStub(omit_tool_use=True) as anthropic:
        with pytest.raises(RuntimeError, match="did not contain the expected tool call"):
            cloud_director._anthropic_call(
                "brief", encoded, schema, model="claude-sonnet-5", api_key="test-key", api_base_url=anthropic.base_url
            )


def test_session_provider_loads_authored_direction_without_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    project = tmp_path / "project"
    (project / "directions").mkdir(parents=True)
    (project / "requests").mkdir()
    (project / "directions" / "scene.json").write_text(json.dumps(VALID_PANEL), encoding="utf-8")
    brief = tmp_path / "panel.txt"
    brief.write_text("scene", encoding="utf-8")
    reference = tmp_path / "ref.png"
    reference.write_bytes(b"png")
    output = project / "requests" / "scene.json"

    result = cloud_director.run_direction(brief, [reference], output, provider="session")

    assert result == VALID_PANEL and json.loads(output.read_text(encoding="utf-8")) == VALID_PANEL
    (project / "directions" / "scene.json").unlink()
    with pytest.raises(RuntimeError, match="Session direction missing"):
        cloud_director.run_direction(brief, [reference], output, provider="session")
