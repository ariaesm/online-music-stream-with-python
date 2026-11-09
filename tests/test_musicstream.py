import http.client
import json
import os
import threading
from pathlib import Path

import pytest

from musicstream.demo import write_tone_wav
from musicstream.library import UnsafePathError, safe_join, scan_library, title_from_filename
from musicstream.ranges import RangeNotSatisfiable, parse_range
from musicstream.server import make_server

# ----------------------------------------------------------------- ranges


@pytest.mark.parametrize(
    "header,expected",
    [
        ("bytes=0-99", (0, 99)),
        ("bytes=100-", (100, 999)),
        ("bytes=-100", (900, 999)),
        ("bytes=-5000", (0, 999)),
        ("bytes=990-5000", (990, 999)),
        ("bytes=999-999", (999, 999)),
        ("BYTES = 5-9", (5, 9)),
    ],
)
def test_parse_range_valid(header, expected):
    assert parse_range(header, 1000) == expected


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "bytes",
        "bytes=",
        "bytes=-",
        "bytes=a-b",
        "items=0-1",
        "bytes=5-1",
        "bytes=0-1,5-6",
    ],
)
def test_parse_range_ignored(header):
    assert parse_range(header, 1000) is None


@pytest.mark.parametrize("header", ["bytes=1000-", "bytes=2000-3000", "bytes=-0"])
def test_parse_range_unsatisfiable(header):
    with pytest.raises(RangeNotSatisfiable):
        parse_range(header, 1000)


# -------------------------------------------------------------- path safety


@pytest.fixture
def library(tmp_path):
    lib = tmp_path / "music"
    (lib / "album").mkdir(parents=True)
    write_tone_wav(lib / "song_one.wav", [440.0], 0.5, sample_rate=8000)
    write_tone_wav(lib / "album" / "two.wav", [330.0, 220.0], 0.25, sample_rate=8000)
    (lib / "notes.txt").write_text("not audio")
    (lib / ".hidden.wav").write_bytes(b"x")
    (tmp_path / "secret.wav").write_bytes(b"outside")
    return lib


def test_safe_join_accepts_inside(library):
    assert safe_join(library, "album/two.wav") == (library / "album" / "two.wav").resolve()
    assert safe_join(library, "./song_one.wav").name == "song_one.wav"


@pytest.mark.parametrize(
    "bad",
    [
        "../secret.wav",
        "album/../../secret.wav",
        "/etc/passwd",
        "..",
        "",
        ".hidden.wav",
        "a\x00b.wav",
        "..\\secret.wav",
    ],
)
def test_safe_join_rejects(library, bad):
    with pytest.raises(UnsafePathError):
        safe_join(library, bad)


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unsupported")
def test_safe_join_rejects_symlink_escape(library, tmp_path):
    link = library / "link.wav"
    try:
        link.symlink_to(tmp_path / "secret.wav")
    except OSError:
        pytest.skip("cannot create symlink")
    with pytest.raises(UnsafePathError):
        safe_join(library, "link.wav")
    assert "link.wav" not in [t.filename for t in scan_library(library)]


def test_scan_library(library):
    tracks = scan_library(library)
    assert [t.path for t in tracks] == ["album/two.wav", "song_one.wav"]
    one = tracks[1]
    assert one.title == "song one"
    assert one.duration == pytest.approx(0.5)
    assert one.size == 44 + 8000 * 0.5 * 2  # header + 16-bit mono samples
    assert one.mime == "audio/wav" and one.url == "/media/song_one.wav"
    assert tracks[0].duration == pytest.approx(0.5)


def test_title_from_filename():
    assert title_from_filename("01_-_My  song.mp3") == "01 - My song"


# --------------------------------------------------------------- live server


@pytest.fixture
def server(library):
    srv = make_server(library, "127.0.0.1", 0)
    thread = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def request(server, path, method="GET", headers=None):
    conn = http.client.HTTPConnection(*server.server_address[:2], timeout=5)
    conn.request(method, path, headers=headers or {})
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    return resp, body


def test_index_page(server):
    resp, body = request(server, "/")
    assert resp.status == 200
    assert resp.getheader("Content-Type").startswith("text/html")
    assert b"/api/tracks" in body


def test_api_tracks(server):
    resp, body = request(server, "/api/tracks")
    assert resp.status == 200
    data = json.loads(body)
    assert data["count"] == 2
    assert {t["filename"] for t in data["tracks"]} == {"song_one.wav", "two.wav"}


def test_full_download(server, library):
    resp, body = request(server, "/media/song_one.wav")
    assert resp.status == 200
    assert resp.getheader("Accept-Ranges") == "bytes"
    assert body == (library / "song_one.wav").read_bytes()


def test_range_request(server, library):
    data = (library / "song_one.wav").read_bytes()
    resp, body = request(server, "/media/song_one.wav", headers={"Range": "bytes=100-199"})
    assert resp.status == 206
    assert resp.getheader("Content-Range") == f"bytes 100-199/{len(data)}"
    assert resp.getheader("Content-Length") == "100"
    assert body == data[100:200]


def test_suffix_and_open_ranges(server, library):
    data = (library / "album" / "two.wav").read_bytes()
    resp, body = request(server, "/media/album/two.wav", headers={"Range": "bytes=-10"})
    assert resp.status == 206 and body == data[-10:]
    resp, body = request(server, "/media/album/two.wav", headers={"Range": "bytes=4000-"})
    assert resp.status == 206 and body == data[4000:]


def test_unsatisfiable_range(server, library):
    size = (library / "song_one.wav").stat().st_size
    resp, body = request(server, "/media/song_one.wav", headers={"Range": f"bytes={size}-"})
    assert resp.status == 416
    assert resp.getheader("Content-Range") == f"bytes */{size}"
    assert body == b""


def test_head_request(server, library):
    resp, body = request(server, "/media/song_one.wav", method="HEAD")
    assert resp.status == 200 and body == b""
    assert int(resp.getheader("Content-Length")) == (library / "song_one.wav").stat().st_size


@pytest.mark.parametrize(
    "path",
    [
        "/media/../secret.wav",
        "/media/%2e%2e/secret.wav",
        "/media/album/%2e%2e/%2e%2e/secret.wav",
        "/media/%2fetc%2fpasswd",
        "/media/.hidden.wav",
        "/media/missing.wav",
        "/nope",
    ],
)
def test_traversal_and_missing_are_404(server, path):
    resp, body = request(server, path)
    assert resp.status == 404
    assert b"outside" not in body


def test_make_server_requires_directory(tmp_path):
    with pytest.raises(NotADirectoryError):
        make_server(Path(tmp_path / "nope"), port=0)
