"""Generate small demo WAV files with the standard library (no numpy needed)."""

from __future__ import annotations

import argparse
import math
import struct
import wave
from pathlib import Path

# (file name, notes in Hz, seconds per note)
DEMO_TRACKS = [
    ("01 - C major arpeggio.wav", [261.63, 329.63, 392.0, 523.25], 0.5),
    ("02 - A minor walk.wav", [220.0, 246.94, 261.63, 293.66, 329.63], 0.4),
    ("03 - Slow fifths.wav", [196.0, 293.66, 196.0, 293.66], 1.0),
]


def write_tone_wav(
    path: str | Path, notes: list[float], note_seconds: float, sample_rate: int = 22050
) -> Path:
    """Write a mono 16-bit WAV that plays ``notes`` one after another."""
    path = Path(path)
    frames = bytearray()
    n = int(sample_rate * note_seconds)
    fade = max(1, int(0.01 * sample_rate))
    for freq in notes:
        for i in range(n):
            env = min(1.0, i / fade, (n - 1 - i) / fade)
            sample = 0.4 * env * math.sin(2 * math.pi * freq * i / sample_rate)
            frames += struct.pack("<h", int(sample * 32767))
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(bytes(frames))
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create demo WAV tracks")
    parser.add_argument("folder", nargs="?", default="music")
    args = parser.parse_args(argv)
    folder = Path(args.folder)
    folder.mkdir(parents=True, exist_ok=True)
    for name, notes, secs in DEMO_TRACKS:
        print("wrote", write_tone_wav(folder / name, notes, secs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
