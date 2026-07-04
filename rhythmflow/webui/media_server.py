from __future__ import annotations

import logging
import mimetypes
import secrets
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

logger = logging.getLogger(__name__)

_CHUNK = 512 * 1024

mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("application/javascript", ".mjs")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("application/wasm", ".wasm")


class MediaServer:
    def __init__(self, static_dir: Path | None) -> None:
        self.static_dir = static_dir.resolve() if static_dir is not None else None
        self._path_to_token: dict[str, str] = {}
        self._token_to_path: dict[str, str] = {}
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._port = 0

    @property
    def port(self) -> int:
        return self._port

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._port}"

    def start(self) -> str:
        handler = _make_handler(self)
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._port = self._server.server_address[1]
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="rhythmflow-media-server",
            daemon=True,
        )
        self._thread.start()
        logger.info("Media server listening on %s (static=%s)", self.base_url, self.static_dir)
        return self.base_url

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            logger.info("Media server stopped")
            self._server = None

    def register(self, path: str) -> str:
        resolved = str(Path(path))
        with self._lock:
            token = self._path_to_token.get(resolved)
            if token is None:
                token = secrets.token_urlsafe(16)
                self._path_to_token[resolved] = token
                self._token_to_path[token] = resolved
        return f"{self.base_url}/media/{token}"

    def resolve(self, token: str) -> str | None:
        with self._lock:
            return self._token_to_path.get(token)


def _make_handler(server: MediaServer) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args: object) -> None:  # noqa: N802 - quiet default logging
            return

        def _cors(self) -> None:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Range")
            self.send_header("Access-Control-Expose-Headers", "Content-Range, Content-Length")

        def do_OPTIONS(self) -> None:  # noqa: N802
            self.send_response(HTTPStatus.NO_CONTENT)
            self._cors()
            self.end_headers()

        def do_HEAD(self) -> None:  # noqa: N802
            self._handle(head_only=True)

        def do_GET(self) -> None:  # noqa: N802
            self._handle(head_only=False)

        def _handle(self, *, head_only: bool) -> None:
            path = unquote(urlparse(self.path).path)
            if path.startswith("/media/"):
                self._serve_media(path[len("/media/"):], head_only=head_only)
                return
            self._serve_static(path, head_only=head_only)

        def _serve_media(self, token: str, *, head_only: bool) -> None:
            file_path = server.resolve(token)
            if not file_path or not Path(file_path).is_file():
                self.send_error(HTTPStatus.NOT_FOUND, "Unknown media token")
                return
            self._send_file(Path(file_path), head_only=head_only, allow_range=True)

        def _serve_static(self, path: str, *, head_only: bool) -> None:
            if server.static_dir is None:
                self.send_error(HTTPStatus.NOT_FOUND, "No static directory configured")
                return
            relative = path.lstrip("/")
            candidate = (server.static_dir / relative).resolve() if relative else server.static_dir / "index.html"
            if relative == "" or not _within(candidate, server.static_dir) or not candidate.is_file():
                candidate = server.static_dir / "index.html"
            if not candidate.is_file():
                self.send_error(HTTPStatus.NOT_FOUND, "Not found")
                return
            self._send_file(candidate, head_only=head_only, allow_range=False)

        def _send_file(self, file_path: Path, *, head_only: bool, allow_range: bool) -> None:
            try:
                size = file_path.stat().st_size
            except OSError:
                self.send_error(HTTPStatus.NOT_FOUND, "Not found")
                return
            content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
            range_header = self.headers.get("Range") if allow_range else None
            start, end = _parse_range(range_header, size)

            if range_header and start is not None:
                self.send_response(HTTPStatus.PARTIAL_CONTENT)
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                length = end - start + 1
            else:
                self.send_response(HTTPStatus.OK)
                start, length = 0, size

            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            if allow_range:
                self.send_header("Accept-Ranges", "bytes")
                self._cors()
            else:
                self.send_header("Cache-Control", "no-cache")
            self.end_headers()

            if head_only:
                return
            try:
                with file_path.open("rb") as handle:
                    handle.seek(start)
                    remaining = length
                    while remaining > 0:
                        chunk = handle.read(min(_CHUNK, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                logger.debug("Client closed connection while streaming %s", file_path)

    return Handler


def _within(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def _parse_range(range_header: str | None, size: int) -> tuple[int | None, int | None]:
    if not range_header or not range_header.startswith("bytes=") or size <= 0:
        return None, None
    spec = range_header[len("bytes="):].split(",")[0].strip()
    if "-" not in spec:
        return None, None
    start_text, end_text = spec.split("-", 1)
    try:
        if start_text == "":
            length = int(end_text)
            start = max(0, size - length)
            return start, size - 1
        start = int(start_text)
        end = int(end_text) if end_text else size - 1
    except ValueError:
        return None, None
    start = max(0, start)
    end = min(end, size - 1)
    if start > end:
        return None, None
    return start, end
