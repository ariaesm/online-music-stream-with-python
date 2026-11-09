"""Music library: safe path resolution, scanning and basic metadata."""

from __future__ import annotations

import mimetypes
import wave
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import quote

AUDIO_TYPES = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".opus": "audio/ogg",
    ".flac": "audio/flac",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".webm": "audio/webm",
}


class UnsafePathError(ValueError):
    """Raised when a requested path points outside the library."""


def safe_join(root: str | Path, relative: str) -> Path:
    """Resolve ``relative`` inside ``root`` or raise :class:`UnsafePathError`.

    Rejects absolute paths, ``..`` escapes, NUL bytes, hidden path components and
    symlinks that resolve outside the library. ``relative`` must already be
    URL-decoded.
    """
    root = Path(root).resolve()
    if "\x00" in relative or "\\" in relative:
        raise UnsafePathError("invalid character in path")
    if relative.startswith("/"):
        raise UnsafePathError("absolute path")
    parts = [p for p in relative.split("/") if p not in ("", ".")]
    if not parts:
        raise UnsafePathError("empty path")
    if any(p == ".." or p.startswith(".") for p in parts):
        raise UnsafePathError("parent or hidden path component")
    candidate = root.joinpath(*parts).resolve()
    if not candidate.is_relative_to(root) or candidate == root:
        raise UnsafePathError("path escapes the library")
    return candidate


def wav_duration(path: Path) -> float | None:
    """Duration in seconds for PCM WAV files, ``None`` if unreadable."""
    try:
        with wave.open(str(path), "rb") as wf:
            rate = wf.getframerate()
            return round(wf.getnframes() / rate, 3) if rate else None
    except (wave.Error, EOFError, OSError):
        return None


def content_type(path: Path) -> str:
    return (
        AUDIO_TYPES.get(path.suffix.lower())
        or mimetypes.guess_type(path.name)[0]
        or "application/octet-stream"
    )


@dataclass
class Track:
    id: int
    title: str
    path: str  # relative POSIX path inside the library
    filename: str
    size: int
    duration: float | None
    mime: str
    url: str

    def to_dict(self) -> dict:
        return asdict(self)


def title_from_filename(name: str) -> str:
    stem = Path(name).stem.replace("_", " ").strip()
    return " ".join(stem.split()) or name


def scan_library(root: str | Path) -> list[Track]:
    """Return audio files under ``root`` (recursive, hidden entries skipped), sorted by path."""
    root = Path(root).resolve()
    tracks: list[Track] = []
    files = sorted(
        p
        for p in root.rglob("*")
        if p.is_file()
        and p.suffix.lower() in AUDIO_TYPES
        and not any(part.startswith(".") for part in p.relative_to(root).parts)
        and p.resolve().is_relative_to(root)
    )
    for i, p in enumerate(files):
        rel = p.relative_to(root).as_posix()
        tracks.append(
            Track(
                id=i,
                title=title_from_filename(p.name),
                path=rel,
                filename=p.name,
                size=p.stat().st_size,
                duration=wav_duration(p) if p.suffix.lower() == ".wav" else None,
                mime=content_type(p),
                url="/media/" + quote(rel),
            )
        )
    return tracks
