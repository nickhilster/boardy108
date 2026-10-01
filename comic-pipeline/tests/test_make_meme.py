import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import make_meme
import meme_sources

TEMPLATE = {
    "source": "imgflip",
    "source_id": "1",
    "name": "Drake Hotline Bling",
    "source_url": "http://example.test/template.jpg",
    "width": 40,
    "height": 30,
    "box_count": 2,
}

MEME_NO_REMIX = {
    "concept_summary": "caption only",
    "remix": {"enabled": False, "positive_prompt": "", "locked_elements": []},
    "captions": [{"box_index": 0, "text": "TOP"}, {"box_index": 1, "text": "BOTTOM"}],
}

MEME_WITH_REMIX = {
    "concept_summary": "remix it",
    "remix": {
        "enabled": True,
        "positive_prompt": "swap the record label for a stock ticker",
        "locked_elements": ["two-panel layout"],
    },
    "captions": [{"box_index": 0, "text": "TOP"}, {"box_index": 1, "text": "BOTTOM"}],
}

FONT_PATH = Path(r"C:\Windows\Fonts\impact.ttf")
if not FONT_PATH.is_file():
    FONT_PATH = Path(r"C:\Windows\Fonts\Impact.ttf")

requires_font = pytest.mark.skipif(
    not FONT_PATH.is_file(), reason="Impact font not available on this machine"
)


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
    def __init__(self, image_filename: str = "meme_00001_.png", image_subfolder: str = "meme-project"):
        self.requests = []
        self.image_filename = image_filename
        self.image_subfolder = image_subfolder
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                owner.requests.append(("GET", self.path, None))
                if self.path == "/system_stats":
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


def _fake_download_template(template, cache_dir, **_kwargs):
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / "fixture-template.png"
    if not path.is_file():
        Image.new("RGB", (template["width"], template["height"]), color=(5, 5, 5)).save(path)
    return path, "fixture-hash"


@requires_font
def test_make_meme_skips_comfyui_when_remix_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(meme_sources, "download_template", _fake_download_template)
    project = tmp_path / "meme-project"

    with OllamaStub(json.dumps(MEME_NO_REMIX)) as ollama:
        record = make_meme.make_meme(
            project,
            "meme-01",
            brief_text="a brief about nothing",
            seed=1,
            template=TEMPLATE,
            ollama_url=ollama.base_url,
            comfy_url="http://127.0.0.1:1",  # unreachable on purpose: proves ComfyUI is never contacted
            log=lambda _message: None,
        )

    assert record["remix"]["enabled"] is False
    assert Path(record["output_path"]).is_file()
    assert not (project / "requests" / "meme-01-workflow.json").exists()
    assert not (project / "requests" / "meme-01-remix.png").exists()
    saved = json.loads((project / "requests" / "meme-01.json").read_text(encoding="utf-8"))
    assert saved["template"]["source_id"] == "1"
    assert saved["seed"] == 1


@requires_font
def test_make_meme_forces_remix_off_when_allow_remix_false(tmp_path, monkeypatch):
    monkeypatch.setattr(meme_sources, "download_template", _fake_download_template)
    project = tmp_path / "meme-project"

    with OllamaStub(json.dumps(MEME_WITH_REMIX)) as ollama:
        record = make_meme.make_meme(
            project,
            "meme-01",
            brief_text="brief",
            seed=1,
            template=TEMPLATE,
            allow_remix=False,
            ollama_url=ollama.base_url,
            comfy_url="http://127.0.0.1:1",
            log=lambda _message: None,
        )

    assert record["remix"]["enabled"] is False  # director wanted it on; the toggle overrides it
    assert not (project / "requests" / "meme-01-workflow.json").exists()


@requires_font
def test_make_meme_full_chain_with_remix(tmp_path, monkeypatch):
    monkeypatch.setattr(meme_sources, "download_template", _fake_download_template)
    project = tmp_path / "meme-project"

    comfy_output_root = tmp_path / "comfy-output"
    rendered_dir = comfy_output_root / "meme-project"
    rendered_dir.mkdir(parents=True)
    Image.new("RGB", (40, 30), color=(9, 9, 9)).save(rendered_dir / "meme_00001_.png")

    with OllamaStub(json.dumps(MEME_WITH_REMIX)) as ollama, ComfyStub(image_subfolder="meme-project") as comfy:
        record = make_meme.make_meme(
            project,
            "meme-01",
            brief_text="brief",
            seed=1,
            template=TEMPLATE,
            ollama_url=ollama.base_url,
            comfy_url=comfy.base_url,
            comfy_output_root=comfy_output_root,
            render_timeout=5,
            log=lambda _message: None,
        )

    assert record["remix"]["enabled"] is True
    assert Path(record["output_path"]).is_file()
    assert (project / "requests" / "meme-01-workflow.json").exists()
    assert (project / "requests" / "meme-01-remix.png").is_file()


@requires_font
def test_make_meme_picks_a_template_when_none_given(tmp_path, monkeypatch):
    monkeypatch.setattr(meme_sources, "download_template", _fake_download_template)
    monkeypatch.setattr(meme_sources, "fetch_top_templates", lambda **_kwargs: [TEMPLATE])
    project = tmp_path / "meme-project"

    with OllamaStub(json.dumps(MEME_NO_REMIX)) as ollama:
        record = make_meme.make_meme(
            project,
            "meme-01",
            brief_text="brief",
            seed=1,
            ollama_url=ollama.base_url,
            comfy_url="http://127.0.0.1:1",
            log=lambda _message: None,
        )

    assert record["template"]["source_id"] == "1"


def test_make_meme_writes_failure_sidecar_when_comfyui_is_unreachable(tmp_path, monkeypatch):
    monkeypatch.setattr(meme_sources, "download_template", _fake_download_template)
    project = tmp_path / "meme-project"

    with OllamaStub(json.dumps(MEME_WITH_REMIX)) as ollama:
        with pytest.raises(RuntimeError, match="Start ComfyUI first"):
            make_meme.make_meme(
                project,
                "meme-01",
                brief_text="brief",
                seed=1,
                template=TEMPLATE,
                ollama_url=ollama.base_url,
                comfy_url="http://127.0.0.1:1",
                log=lambda _message: None,
            )

    failure = json.loads((project / "requests" / "meme-01-failure.json").read_text(encoding="utf-8"))
    assert failure["name"] == "meme-01"


@requires_font
def test_make_meme_clears_stale_failure_sidecar_on_successful_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(meme_sources, "download_template", _fake_download_template)
    project = tmp_path / "meme-project"

    # First attempt: remix requested but ComfyUI is unreachable -> fails,
    # writes a failure sidecar.
    with OllamaStub(json.dumps(MEME_WITH_REMIX)) as ollama:
        with pytest.raises(RuntimeError, match="Start ComfyUI first"):
            make_meme.make_meme(
                project,
                "meme-01",
                brief_text="brief",
                seed=1,
                template=TEMPLATE,
                ollama_url=ollama.base_url,
                comfy_url="http://127.0.0.1:1",
                log=lambda _message: None,
            )
    failure_path = project / "requests" / "meme-01-failure.json"
    assert failure_path.exists()

    # Retry with remix disabled -> never touches ComfyUI, succeeds even
    # though comfy_url is still unreachable. The stale failure sidecar from
    # the first attempt must not linger and mislead a later check.
    with OllamaStub(json.dumps(MEME_NO_REMIX)) as ollama:
        make_meme.make_meme(
            project,
            "meme-01",
            brief_text="brief",
            seed=1,
            template=TEMPLATE,
            allow_remix=False,
            ollama_url=ollama.base_url,
            comfy_url="http://127.0.0.1:1",
            log=lambda _message: None,
        )

    assert not failure_path.exists()
