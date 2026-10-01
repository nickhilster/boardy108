import hashlib
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import meme_sources

IMAGE_BYTES = b"fake-jpeg-bytes-for-template"


class ImgflipStub:
    def __init__(self, memes: list[dict] | None = None, *, success: bool = True):
        self.memes = memes or []
        self.success = success
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/get_memes":
                    payload = json.dumps(
                        {"success": owner.success, "data": {"memes": owner.memes}}
                    ).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                elif self.path == "/template.jpg":
                    self.send_response(200)
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", str(len(IMAGE_BYTES)))
                    self.end_headers()
                    self.wfile.write(IMAGE_BYTES)
                else:
                    self.send_error(404)

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


def _raw_meme(base_url: str, **overrides) -> dict:
    raw = {
        "id": "181913649",
        "name": "Drake Hotline Bling",
        "url": f"{base_url}/template.jpg",
        "width": 1200,
        "height": 1200,
        "box_count": 2,
    }
    raw.update(overrides)
    return raw


def test_normalize_template_maps_fields():
    normalized = meme_sources.normalize_template(_raw_meme("http://example.test"))
    assert normalized == {
        "source": "imgflip",
        "source_id": "181913649",
        "name": "Drake Hotline Bling",
        "source_url": "http://example.test/template.jpg",
        "width": 1200,
        "height": 1200,
        "box_count": 2,
    }


def test_normalize_template_requires_all_fields():
    with pytest.raises(ValueError, match="box_count"):
        meme_sources.normalize_template({"id": "1", "name": "x", "url": "u", "width": 1, "height": 1})


def test_fetch_top_templates_normalizes_and_respects_limit():
    with ImgflipStub() as stub:
        stub.memes = [_raw_meme(stub.base_url, id="1"), _raw_meme(stub.base_url, id="2")]
        templates = meme_sources.fetch_top_templates(base_url=stub.base_url, limit=1)
    assert len(templates) == 1
    assert templates[0]["source_id"] == "1"
    assert templates[0]["source"] == "imgflip"


def test_fetch_top_templates_raises_when_imgflip_reports_failure():
    with ImgflipStub(success=False) as stub:
        with pytest.raises(RuntimeError, match="failure"):
            meme_sources.fetch_top_templates(base_url=stub.base_url)


def test_pick_template_by_source_id():
    templates = [
        meme_sources.normalize_template(_raw_meme("http://example.test", id="1")),
        meme_sources.normalize_template(_raw_meme("http://example.test", id="2")),
    ]
    picked = meme_sources.pick_template(templates, source_id="2")
    assert picked["source_id"] == "2"


def test_pick_template_raises_for_unknown_source_id():
    templates = [meme_sources.normalize_template(_raw_meme("http://example.test"))]
    with pytest.raises(ValueError, match="source_id"):
        meme_sources.pick_template(templates, source_id="does-not-exist")


def test_pick_template_raises_for_empty_list():
    with pytest.raises(ValueError):
        meme_sources.pick_template([])


def test_download_template_caches_by_hash(tmp_path):
    with ImgflipStub() as stub:
        template = meme_sources.normalize_template(_raw_meme(stub.base_url))
        path1, digest1 = meme_sources.download_template(template, tmp_path)
        assert path1.is_file()
        assert path1.read_bytes() == IMAGE_BYTES
        assert digest1 == hashlib.sha256(IMAGE_BYTES).hexdigest()

        path2, digest2 = meme_sources.download_template(template, tmp_path)
        assert path2 == path1
        assert digest2 == digest1
