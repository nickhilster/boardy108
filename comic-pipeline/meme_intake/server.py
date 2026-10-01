"""Local, loopback-only intake GUI for the meme generator.

Serves a single-page form and a synchronous POST /generate endpoint that
calls make_meme.make_meme(...) directly in the request handler thread -- no
async job queue, no separate producer agent to hand off to, matching
make_panel.py's synchronous one-call shape rather than Audio Studio's
async filesystem-handoff intake design. Security posture (loopback-only
bind, CSRF double-submit token, Origin/Host checks, project-path
containment) is borrowed from that design even though the handoff itself
is simpler here.
"""

from __future__ import annotations

import argparse
import base64
import json
import secrets
import sys
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # allow running this file directly

import make_meme  # noqa: E402
import meme_sources  # noqa: E402
import pipeline_config  # noqa: E402

STATIC_DIR = Path(__file__).with_name("static")
CSRF_COOKIE_NAME = "panelforge_meme_csrf"
LOOPBACK_HOSTNAMES = ("127.0.0.1", "localhost", "::1")
TEMPLATE_FIELDS = ("source", "source_id", "name", "source_url", "width", "height", "box_count")


class IntakeError(Exception):
    def __init__(self, status: HTTPStatus, message: str):
        super().__init__(message)
        self.status = status


def _is_loopback_host(host_header: str | None) -> bool:
    if not host_header:
        return False
    host = host_header.split(":", 1)[0].strip("[]")
    return host in LOOPBACK_HOSTNAMES


def make_handler(*, projects_root: Path, csrf_token: str) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "PanelForgeMemeIntake/1"

        def log_message(self, *_args: Any) -> None:
            return

        def _check_origin(self) -> None:
            if not _is_loopback_host(self.headers.get("Host")):
                raise IntakeError(HTTPStatus.FORBIDDEN, "Host must be loopback")
            origin = self.headers.get("Origin")
            if origin is not None and urlparse(origin).hostname not in LOOPBACK_HOSTNAMES:
                raise IntakeError(HTTPStatus.FORBIDDEN, "Origin not allowed")

        def _check_csrf(self, body: dict[str, Any]) -> None:
            jar = SimpleCookie()
            jar.load(self.headers.get("Cookie", ""))
            stored = jar.get(CSRF_COOKIE_NAME)
            supplied = body.get("csrf_token") or self.headers.get("X-CSRF-Token")
            if not supplied or stored is None or stored.value != csrf_token or supplied != csrf_token:
                raise IntakeError(HTTPStatus.FORBIDDEN, "Missing or invalid CSRF token")

        def _resolve_project_dir(self, project_name: str) -> Path:
            project_name = project_name.strip()
            if not project_name or "/" in project_name or "\\" in project_name or ".." in project_name:
                raise IntakeError(HTTPStatus.BAD_REQUEST, "Invalid project name")
            project_dir = (projects_root / project_name).resolve()
            if project_dir != projects_root and projects_root not in project_dir.parents:
                raise IntakeError(HTTPStatus.BAD_REQUEST, "Project path escapes the projects root")
            return project_dir

        def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
            encoded = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler naming)
            try:
                self._check_origin()
                if self.path == "/":
                    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
                    html = html.replace("__CSRF_TOKEN__", csrf_token)
                    encoded = html.encode("utf-8")
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(encoded)))
                    self.send_header(
                        "Set-Cookie",
                        f"{CSRF_COOKIE_NAME}={csrf_token}; HttpOnly; SameSite=Strict; Path=/",
                    )
                    self.end_headers()
                    self.wfile.write(encoded)
                elif self.path == "/templates":
                    self._send_json(HTTPStatus.OK, {"templates": meme_sources.fetch_top_templates()})
                else:
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            except IntakeError as error:
                self._send_json(error.status, {"error": str(error)})
            except Exception as error:  # never leak a stack trace to the client
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(error)})

        def do_POST(self) -> None:  # noqa: N802
            try:
                self._check_origin()
                if self.path != "/generate":
                    self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                    return
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                self._check_csrf(body)

                project_dir = self._resolve_project_dir(str(body.get("project") or ""))
                brief_text = str(body.get("brief") or "").strip()
                if not brief_text:
                    raise IntakeError(HTTPStatus.BAD_REQUEST, "Brief is required")
                name = str(body.get("name") or "meme").strip() or "meme"
                seed_value = body.get("seed")
                seed = int(seed_value) if seed_value not in (None, "") else secrets.randbelow(2**31)
                allow_remix = bool(body.get("allow_remix", True))

                template = body.get("template") or None
                if template is not None:
                    missing = [field for field in TEMPLATE_FIELDS if field not in template]
                    if missing:
                        raise IntakeError(HTTPStatus.BAD_REQUEST, f"Template is missing fields: {missing}")

                try:
                    record = make_meme.make_meme(
                        project_dir,
                        name,
                        brief_text=brief_text,
                        seed=seed,
                        template=template,
                        allow_remix=allow_remix,
                        log=lambda _message: None,
                    )
                except (ValueError, RuntimeError, TimeoutError) as error:
                    raise IntakeError(HTTPStatus.BAD_GATEWAY, str(error)) from error

                image_bytes = Path(record["output_path"]).read_bytes()
                data_url = "data:image/png;base64," + base64.b64encode(image_bytes).decode("ascii")
                self._send_json(HTTPStatus.OK, {"image": data_url, "meme": record})
            except IntakeError as error:
                self._send_json(error.status, {"error": str(error)})
            except Exception as error:
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(error)})

    return Handler


def serve(
    *, host: str = "127.0.0.1", port: int | None = None, projects_root: Path | str | None = None
) -> ThreadingHTTPServer:
    if host not in LOOPBACK_HOSTNAMES:
        raise ValueError("The meme intake GUI only binds to loopback addresses")
    resolved_port = pipeline_config.meme_intake_port(port)
    resolved_root = Path(projects_root or pipeline_config.get("meme_projects_root") or Path.cwd()).resolve()
    csrf_token = secrets.token_urlsafe(32)
    handler = make_handler(projects_root=resolved_root, csrf_token=csrf_token)
    return ThreadingHTTPServer((host, resolved_port), handler)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, help=f"default: {pipeline_config.DEFAULT_MEME_INTAKE_PORT}")
    parser.add_argument(
        "--projects-root", type=Path, help="Directory containing meme project folders (default: current directory)"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    server = serve(port=args.port, projects_root=args.projects_root)
    host, port = server.server_address
    print(f"Meme intake GUI listening on http://{host}:{port}  (Ctrl+C to stop)", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
