import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import direct_panel
import canon


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


def test_build_request_includes_canon_and_verbatim_brief_without_changing_images(tmp_path):
    reference = tmp_path / "boardy.png"
    reference.write_bytes(b"fake-png")
    brief = "  Boardy in the rain.\n\nKeep this spacing.  \n"
    project = Path(__file__).resolve().parents[1] / "projects" / "boardy-108"
    value = canon.load_canon(project)
    block = canon.format_instructions(value)
    original = direct_panel.build_request("qwen3-vl:8b", brief, [reference])
    request = direct_panel.build_request("qwen3-vl:8b", brief, [reference], canon_instructions=block)
    content = request["messages"][0]["content"]
    assert content.endswith(f"{block}\n\nPANEL BRIEF:\n{brief}")
    for field in ("locked_traits", "allowed_variations", "prohibitions"):
        assert all(item in content for item in value[field])
    assert request["messages"][0]["images"] == original["messages"][0]["images"]
    assert direct_panel.build_request("qwen3-vl:8b", brief, [reference], canon_instructions=None) == original
    assert original["messages"][0]["content"].endswith(f"PANEL BRIEF:\n{brief.strip()}")


class OllamaStub:
    def __init__(
        self,
        chat_content: str = "",
        chat_thinking: str = "",
        chat_responses: list[tuple[str, str]] | None = None,
    ):
        self.chat_responses = (
            list(chat_responses) if chat_responses is not None else [(chat_content, chat_thinking)]
        )
        self.requests = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers["Content-Length"])
                body = json.loads(self.rfile.read(length))
                owner.requests.append((self.path, body))
                if self.path == "/api/chat":
                    chat_calls = sum(1 for path, _body in owner.requests if path == "/api/chat")
                    index = min(chat_calls - 1, len(owner.chat_responses) - 1)
                    content, thinking = owner.chat_responses[index]
                    response = {
                        "message": {"role": "assistant", "content": content, "thinking": thinking},
                        "done": True,
                    }
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


def test_build_request_contains_schema_and_encoded_reference(tmp_path):
    reference = tmp_path / "mara.png"
    reference.write_bytes(b"fake-png")

    request = direct_panel.build_request("qwen3-vl:8b", "Mara enters.", [reference])

    assert request["model"] == "qwen3-vl:8b"
    assert request["stream"] is False
    assert request["keep_alive"] == 0
    assert request["think"] is False
    assert request["options"] == {
        "num_ctx": 8192,
        "num_predict": 1600,
        "temperature": 0.3,
        "repeat_penalty": 1.3,
        "repeat_last_n": 128,
    }
    assert request["messages"][0]["images"] == ["ZmFrZS1wbmc="]
    assert request["format"]["required"] == list(VALID_PANEL)
    instructions = request["messages"][0]["content"]
    assert "negative_prompt" in instructions and "never applied" in instructions
    assert "positive_prompt" in instructions


@pytest.mark.parametrize("name", ["missing.png", "reference.gif", "reference.txt"])
def test_validate_references_rejects_missing_or_unsupported_files(tmp_path, name):
    candidate = tmp_path / name
    if name != "missing.png":
        candidate.write_bytes(b"data")

    with pytest.raises(ValueError):
        direct_panel.validate_reference_paths([candidate])


def test_validate_panel_rejects_missing_contract_field():
    invalid = dict(VALID_PANEL)
    invalid.pop("continuity")

    with pytest.raises(ValueError, match="continuity"):
        direct_panel.validate_panel(invalid)


def test_validate_panel_allows_empty_negative_prompt():
    panel = dict(VALID_PANEL)
    panel["negative_prompt"] = ""

    validated = direct_panel.validate_panel(panel)

    assert validated["negative_prompt"] == ""


def test_validate_panel_allows_empty_continuity():
    panel = dict(VALID_PANEL)
    panel["continuity"] = ""

    validated = direct_panel.validate_panel(panel)

    assert validated["continuity"] == ""


def test_validate_panel_rejects_empty_positive_prompt():
    invalid = dict(VALID_PANEL)
    invalid["positive_prompt"] = ""

    with pytest.raises(ValueError, match="positive_prompt"):
        direct_panel.validate_panel(invalid)


def test_run_direction_writes_validated_json_and_unloads_model(tmp_path):
    brief = tmp_path / "panel.txt"
    brief.write_text("Mara confronts the courier.", encoding="utf-8")
    reference = tmp_path / "mara.jpg"
    reference.write_bytes(b"jpeg-data")
    output = tmp_path / "request.json"

    with OllamaStub(chat_content=json.dumps(VALID_PANEL)) as ollama:
        result = direct_panel.run_direction(
            brief,
            [reference],
            output,
            model="qwen3-vl:8b",
            base_url=ollama.base_url,
        )

    assert result == VALID_PANEL
    assert json.loads(output.read_text(encoding="utf-8")) == VALID_PANEL
    assert [path for path, _body in ollama.requests] == ["/api/chat", "/api/generate"]
    assert ollama.requests[1][1] == {"model": "qwen3-vl:8b", "keep_alive": 0}


def test_run_direction_sends_optional_canon_to_local_director(tmp_path):
    brief = tmp_path / "scene.txt"
    brief.write_text("  Boardy at sea.\n", encoding="utf-8")
    reference = tmp_path / "boardy.png"
    reference.write_bytes(b"fake-png")
    with OllamaStub(chat_content=json.dumps(VALID_PANEL)) as ollama:
        result = direct_panel.run_direction(
            brief, [reference], tmp_path / "direction.json",
            base_url=ollama.base_url, canon_instructions="CANON BLOCK",
        )
    assert result == VALID_PANEL
    content = ollama.requests[0][1]["messages"][0]["content"]
    assert content.endswith("CANON BLOCK\n\nPANEL BRIEF:\n  Boardy at sea.\n")


def test_run_direction_falls_back_to_thinking_field_when_content_is_empty(tmp_path):
    brief = tmp_path / "panel.txt"
    brief.write_text("Mara confronts the courier.", encoding="utf-8")
    reference = tmp_path / "mara.jpg"
    reference.write_bytes(b"jpeg-data")
    output = tmp_path / "request.json"

    with OllamaStub(chat_content="", chat_thinking=json.dumps(VALID_PANEL)) as ollama:
        result = direct_panel.run_direction(
            brief,
            [reference],
            output,
            model="qwen3-vl:8b",
            base_url=ollama.base_url,
        )

    assert result == VALID_PANEL
    assert json.loads(output.read_text(encoding="utf-8")) == VALID_PANEL


def test_run_direction_retries_after_a_transient_invalid_response(tmp_path):
    brief = tmp_path / "panel.txt"
    brief.write_text("Mara confronts the courier.", encoding="utf-8")
    reference = tmp_path / "mara.jpg"
    reference.write_bytes(b"jpeg-data")
    output = tmp_path / "request.json"

    incomplete_panel = dict(VALID_PANEL)
    incomplete_panel["composition"] = ""
    responses = [(json.dumps(incomplete_panel), ""), (json.dumps(VALID_PANEL), "")]

    with OllamaStub(chat_responses=responses) as ollama:
        result = direct_panel.run_direction(
            brief,
            [reference],
            output,
            model="qwen3-vl:8b",
            base_url=ollama.base_url,
        )

    assert result == VALID_PANEL
    assert [path for path, _body in ollama.requests] == ["/api/chat", "/api/chat", "/api/generate"]


def test_run_direction_unloads_model_and_leaves_no_output_on_invalid_json(tmp_path):
    brief = tmp_path / "panel.txt"
    brief.write_text("Mara confronts the courier.", encoding="utf-8")
    reference = tmp_path / "mara.webp"
    reference.write_bytes(b"webp-data")
    output = tmp_path / "request.json"

    with OllamaStub(chat_content="not-json") as ollama:
        with pytest.raises(ValueError, match="valid JSON"):
            direct_panel.run_direction(
                brief,
                [reference],
                output,
                model="qwen3-vl:8b",
                base_url=ollama.base_url,
            )

    assert not output.exists()
    assert [path for path, _body in ollama.requests] == ["/api/chat"] * 5 + ["/api/generate"]
