"""Threaded HTTP server: player page, JSON track list and seekable audio streaming."""

from __future__ import annotations

import argparse
import json
import logging
from functools import partial
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .library import UnsafePathError, content_type, safe_join, scan_library
from .ranges import RangeNotSatisfiable, parse_range

CHUNK = 64 * 1024
log = logging.getLogger("musicstream")


def load_player_page() -> bytes:
    return resources.files("musicstream").joinpath("static/index.html").read_bytes()


class MusicHandler(BaseHTTPRequestHandler):
    server_version = "musicstream/0.1"
    protocol_version = "HTTP/1.1"  # keep-alive; every response sets Content-Length

    def __init__(self, *args, library: Path, **kwargs):
        self.library = library
        super().__init__(*args, **kwargs)

    # ------------------------------------------------------------ routing
    def do_HEAD(self):  # noqa: N802 (http.server naming)
        self._dispatch(head=True)

    def do_GET(self):  # noqa: N802
        self._dispatch(head=False)

    def _dispatch(self, head: bool) -> None:
        path = urlsplit(self.path).path
        try:
            if path in ("/", "/index.html"):
                self._send_bytes(load_player_page(), "text/html; charset=utf-8", head)
            elif path == "/api/tracks":
                tracks = [t.to_dict() for t in scan_library(self.library)]
                body = json.dumps({"count": len(tracks), "tracks": tracks}).encode()
                self._send_bytes(body, "application/json", head)
            elif path.startswith("/media/"):
                self._send_media(unquote(path[len("/media/") :]), head)
            else:
                self.send_error(HTTPStatus.NOT_FOUND)
        except (BrokenPipeError, ConnectionResetError):
            pass  # the client went away (normal when the player seeks)

    # ----------------------------------------------------------- helpers
    def _security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")

    def _send_bytes(self, body: bytes, ctype: str, head: bool) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self._security_headers()
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def _send_media(self, relative: str, head: bool) -> None:
        try:
            file_path = safe_join(self.library, relative)
        except UnsafePathError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        if not file_path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        size = file_path.stat().st_size
        try:
            byte_range = parse_range(self.headers.get("Range"), size)
        except RangeNotSatisfiable:
            self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        start, end = byte_range if byte_range else (0, size - 1)
        length = max(0, end - start + 1)
        if byte_range:
            self.send_response(HTTPStatus.PARTIAL_CONTENT)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type(file_path))
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self._security_headers()
        self.end_headers()
        if head or length == 0:
            return
        with file_path.open("rb") as fh:
            fh.seek(start)
            remaining = length
            while remaining > 0:
                chunk = fh.read(min(CHUNK, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def log_message(self, fmt, *args):
        log.info("%s - %s", self.address_string(), fmt % args)


def make_server(library: str | Path, host: str = "127.0.0.1", port: int = 8000):
    library = Path(library).resolve()
    if not library.is_dir():
        raise NotADirectoryError(f"library folder not found: {library}")
    handler = partial(MusicHandler, library=library)
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True
    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="musicstream", description="Stream a music folder")
    parser.add_argument("library", nargs="?", default="music", help="folder with audio files")
    parser.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("-q", "--quiet", action="store_true", help="do not log requests")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING if args.quiet else logging.INFO, format="%(message)s")
    try:
        server = make_server(args.library, args.host, args.port)
    except (NotADirectoryError, OSError) as exc:
        print(f"error: {exc}")
        return 1
    count = len(scan_library(args.library))
    host, port = server.server_address[:2]
    print(f"Serving {count} track(s) from {Path(args.library).resolve()} at http://{host}:{port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
    return 0
