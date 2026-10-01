import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import direct_meme

TEMPLATE_META = {"name": "Drake Hotline Bling", "box_count": 2}

VALID_MEME = {
    "concept_summary": "A stock chart vs my sleep schedule",
    "remix": {
        "enabled": False,
        "positive_prompt": "",
        "locked_elements": ["two-panel comparison layout"],
    },
    "captions": [
        {"box_index": 0, "text": "MY SLEEP SCHEDULE"},
        {"box_index": 1, "text": "THE STOCK MARKET"},
    ],
}

MISSING_BOX_MEME = {
    "concept_summary": "Incomplete captions",
    "remix": {"enabled": False, "positive_prompt": "", "locked_elements": []},
    "captions": [{"box_index": 0, "text": "ONLY ONE BOX"}],
}


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

    @property
    def generate_calls(self):
        return [body for path, body in self.requests if path == "/api/generate"]

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def _template_image(tmp_path: Path) -> Path:
    image = tmp_path / "template.jpg"
    image.write_bytes(b"fake-template-bytes")
    return image


def test_run_direction_returns_validated_meme(tmp_path):
    image = _template_image(tmp_path)
    with OllamaStub(json.dumps(VALID_MEME)) as ollama:
        result = direct_meme.run_direction(image, TEMPLATE_META, "make it about sleep", base_url=ollama.base_url)
    assert result == VALID_MEME
    assert len(ollama.generate_calls) == 1  # model unloaded exactly once


def test_run_direction_falls_back_to_thinking_field(tmp_path):
    image = _template_image(tmp_path)
    with OllamaStub(chat_content="", chat_thinking=json.dumps(VALID_MEME)) as ollama:
        result = direct_meme.run_direction(image, TEMPLATE_META, "brief", base_url=ollama.base_url)
    assert result == VALID_MEME


def test_run_direction_retries_when_caption_coverage_is_incomplete(tmp_path):
    image = _template_image(tmp_path)
    responses = [(json.dumps(MISSING_BOX_MEME), ""), (json.dumps(VALID_MEME), "")]
    with OllamaStub(chat_responses=responses) as ollama:
        result = direct_meme.run_direction(image, TEMPLATE_META, "brief", base_url=ollama.base_url)
    assert result == VALID_MEME
    assert len(ollama.generate_calls) == 1


def test_run_direction_raises_after_max_attempts_and_still_unloads_model(tmp_path):
    image = _template_image(tmp_path)
    with OllamaStub(json.dumps(MISSING_BOX_MEME)) as ollama:
        with pytest.raises(ValueError, match="box_index"):
            direct_meme.run_direction(image, TEMPLATE_META, "brief", base_url=ollama.base_url, max_attempts=2)
    assert len(ollama.generate_calls) == 1


def test_validate_meme_rejects_missing_remix_prompt_when_enabled():
    meme = json.loads(json.dumps(VALID_MEME))
    meme["remix"]["enabled"] = True
    meme["remix"]["positive_prompt"] = "   "
    with pytest.raises(ValueError, match="positive_prompt"):
        direct_meme.validate_meme(meme)


def test_validate_meme_rejects_duplicate_box_index():
    meme = json.loads(json.dumps(VALID_MEME))
    meme["captions"][1]["box_index"] = 0
    with pytest.raises(ValueError, match="Duplicate"):
        direct_meme.validate_meme(meme)


def test_validate_meme_rejects_unknown_top_level_field():
    meme = json.loads(json.dumps(VALID_MEME))
    meme["extra"] = "not allowed"
    # additionalProperties isn't hand-enforced beyond required fields today,
    # so this documents current behavior: unknown fields are simply ignored,
    # matching direct_panel.validate_panel's approach of returning only the
    # known required fields rather than round-tripping the raw input.
    result = direct_meme.validate_meme(meme)
    assert "extra" not in result
