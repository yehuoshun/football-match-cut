#!/usr/bin/env python3
"""Media probing helpers.

Uses ffprobe when available, otherwise falls back to parsing `ffmpeg -i` output.
Some minimal containers ship ffmpeg without ffprobe, or with a broken ffprobe shim.
"""
from __future__ import annotations

import re
import shutil
import subprocess


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def duration(path: str) -> float:
    """Duration in seconds."""
    if shutil.which("ffprobe"):
        r = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                  "-of", "default=nw=1:nk=1", path])
        if r.returncode == 0 and r.stdout.strip():
            try:
                return float(r.stdout.strip())
            except ValueError:
                pass
    r = _run(["ffmpeg", "-hide_banner", "-i", path])
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", r.stderr or "")
    if not m:
        raise RuntimeError(f"cannot determine duration of {path!r} (no ffprobe, ffmpeg parse failed)")
    h, mi, s = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(s)


def fps(path: str) -> float:
    """Video frame rate (falls back to 25)."""
    if shutil.which("ffprobe"):
        r = _run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                  "-show_entries", "stream=r_frame_rate", "-of", "default=nw=1:nk=1", path])
        if r.returncode == 0 and r.stdout.strip():
            num, _, den = r.stdout.strip().partition("/")
            try:
                return float(num) / float(den or 1)
            except (ValueError, ZeroDivisionError):
                pass
    r = _run(["ffmpeg", "-hide_banner", "-i", path])
    m = re.search(r"(\d+(?:\.\d+)?)\s*fps", r.stderr or "")
    return float(m.group(1)) if m else 25.0