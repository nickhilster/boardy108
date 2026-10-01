import http.client
import json
import sys
import threading
from http.cookies import SimpleCookie
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from meme_intake import server as intake_server
import make_meme


class ServerFixture:
    def __init__(self, projects_root: Path):
        self.server = intake_server.serve(port=0, projects_root=projects_root)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def host_port(self):
        host, port = self.server.server_address
        return host, port

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def _get(fixture: ServerFixture, path: str):
    host, port = fixture.host_port
    conn = http.client.HTTPConnection(host, port, timeout=5)
    conn.request("GET", path, headers={"Host": f"{host}:{port}"})
    response = conn.getresponse()
    body = response.read()
    conn.close()
    return response, body


def _csrf_and_cookie(fixture: ServerFixture) -> tuple[str, str]:
    response, body = _get(fixture, "/")
    html = body.decode("utf-8")
    token = html.split('name="csrf-token" content="', 1)[1].split('"', 1)[0]
    cookie_header = response.getheader("Set-Cookie")
    cookie = SimpleCookie()
    cookie.load(cookie_header)
    return token, cookie[intake_server.CSRF_COOKIE_NAME].value


def _post(fixture: ServerFixture, path: str, payload: dict, *, cookie: str | None = None, host: str | None = None, origin: str | None = None):
    server_host, port = fixture.host_port
    conn = http.client.HTTPConnection(server_host, port, timeout=5)
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json", "Content-Length": str(len(body))}
    headers["Host"] = host if host is not None else f"{server_host}:{port}"
    if cookie is not None:
        headers["Cookie"] = f"{intake_server.CSRF_COOKIE_NAME}={cookie}"
    if origin is not None:
        headers["Origin"] = origin
    conn.request("POST", path, body=body, headers=headers)
    response = conn.getresponse()
    data = json.loads(response.read())
    conn.close()
    return response, data


def test_get_root_serves_form_with_csrf_token_and_sets_cookie(tmp_path):
    with ServerFixture(tmp_path) as fixture:
        response, body = _get(fixture, "/")
        assert response.status == 200
        assert b"__CSRF_TOKEN__" not in body
        assert response.getheader("Set-Cookie") is not None


def test_generate_rejects_missing_csrf_token(tmp_path):
    with ServerFixture(tmp_path) as fixture:
        response, data = _post(fixture, "/generate", {"project": "p", "brief": "b"})
    assert response.status == 403
    assert "CSRF" in data["error"]


def test_generate_rejects_foreign_origin(tmp_path, monkeypatch):
    with ServerFixture(tmp_path) as fixture:
        token, cookie = _csrf_and_cookie(fixture)
        response, data = _post(
            fixture,
            "/generate",
            {"project": "p", "brief": "b", "csrf_token": token},
            cookie=cookie,
            origin="http://evil.example",
        )
    assert response.status == 403
    assert "Origin" in data["error"]


def test_generate_rejects_path_traversal_project_name(tmp_path):
    with ServerFixture(tmp_path) as fixture:
        token, cookie = _csrf_and_cookie(fixture)
        response, data = _post(
            fixture,
            "/generate",
            {"project": "../escape", "brief": "b", "csrf_token": token},
            cookie=cookie,
        )
    assert response.status == 400
    assert "Invalid project name" in data["error"]


def test_generate_calls_make_meme_and_returns_image(tmp_path, monkeypatch):
    captured = {}

    def fake_make_meme(project_dir, name, **kwargs):
        captured["project_dir"] = Path(project_dir)
        captured["name"] = name
        captured["kwargs"] = kwargs
        output_dir = Path(project_dir) / "outputs"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{name}.png"
        output_path.write_bytes(b"\x89PNGfake")
        return {"output_path": str(output_path), "captions": []}

    monkeypatch.setattr(make_meme, "make_meme", fake_make_meme)

    with ServerFixture(tmp_path) as fixture:
        token, cookie = _csrf_and_cookie(fixture)
        response, data = _post(
            fixture,
            "/generate",
            {
                "project": "my-project",
                "name": "meme-01",
                "brief": "a brief",
                "seed": 5,
                "allow_remix": False,
                "csrf_token": token,
            },
            cookie=cookie,
        )

    assert response.status == 200
    assert data["image"].startswith("data:image/png;base64,")
    assert captured["name"] == "meme-01"
    assert captured["project_dir"] == (tmp_path / "my-project").resolve()
    assert captured["kwargs"]["allow_remix"] is False
    assert captured["kwargs"]["seed"] == 5


def test_generate_treats_seed_zero_as_provided(tmp_path, monkeypatch):
    captured = {}

    def fake_make_meme(project_dir, name, **kwargs):
        captured["seed"] = kwargs["seed"]
        output_dir = Path(project_dir) / "outputs"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{name}.png"
        output_path.write_bytes(b"\x89PNGfake")
        return {"output_path": str(output_path)}

    monkeypatch.setattr(make_meme, "make_meme", fake_make_meme)

    with ServerFixture(tmp_path) as fixture:
        token, cookie = _csrf_and_cookie(fixture)
        response, _data = _post(
            fixture,
            "/generate",
            {"project": "p", "brief": "b", "seed": 0, "csrf_token": token},
            cookie=cookie,
        )

    assert response.status == 200
    assert captured["seed"] == 0
