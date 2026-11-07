#!/usr/bin/env python3
"""S1: split a video into shots with ffmpeg scene detection (+ optional pHash dedupe).

Usage:
    python split_shots.py match.mp4 -o work/shots.json --threshold 0.25 --min-dur 1.0

Output (shots.json):
    {"video": ..., "threshold": ..., "duration": ..., "fps": ...,
     "shots": [{"id": "S0001", "start": 0.0, "end": 12.48, "dur": 12.48, "still": false}]}

"still" marks a shot merged away by pHash dedupe (near-identical to its predecessor);
such shots keep their own id but are flagged so downstream steps can skip them.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from _probe import duration as probe_duration, fps as probe_fps


def detect_scene_times(video: str, threshold: float, workdir: str) -> list[float]:
    """Run ffmpeg scene detection; return sorted cut timestamps in seconds."""
    txt = "scenes.txt"
    cmd = [
        "ffmpeg", "-hide_banner", "-nostdin", "-y", "-i", os.path.abspath(video), "-an",
        "-vf", f"select='gt(scene,{threshold})',metadata=print:file={txt}",
        "-f", "null", "-",
    ]
    subprocess.run(cmd, check=True, capture_output=True, cwd=workdir)
    path = os.path.join(workdir, txt)
    times: list[float] = []
    if os.path.exists(path):
        with open(path, errors="ignore") as fh:
            for line in fh:
                m = re.search(r"pts_time:([0-9.]+)", line)
                if m:
                    times.append(float(m.group(1)))
    return sorted(set(times))


def build_intervals(cuts: list[float], duration: float, min_dur: float) -> list[dict]:
    pts = [0.0] + [t for t in cuts if 0.0 < t < duration] + [duration]
    shots: list[dict] = []
    for start, end in zip(pts, pts[1:]):
        if end - start < min_dur:
            if shots:  # absorb tiny segments into the previous shot
                shots[-1]["end"] = round(end, 3)
                shots[-1]["dur"] = round(shots[-1]["end"] - shots[-1]["start"], 3)
            continue
        shots.append({"start": round(start, 3), "end": round(end, 3),
                      "dur": round(end - start, 3)})
    return shots


def dhash_at(video: str, t: float, tmpdir: str):
    """Perceptual hash of the frame near t. Returns None when deps are missing."""
    try:
        from PIL import Image
        import imagehash
    except ImportError:
        return None
    out = os.path.join(tmpdir, f"k_{int(t * 1000)}.jpg")
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-ss", f"{t:.3f}", "-i", video,
         "-frames:v", "1", "-vf", "scale=320:-2", "-q:v", "5", out],
        check=True, capture_output=True,
    )
    try:
        return imagehash.dhash(Image.open(out))
    finally:
        if os.path.exists(out):
            os.remove(out)


def main() -> int:
    ap = argparse.ArgumentParser(description="Split video into shots (ffmpeg scene detect).")
    ap.add_argument("video", help="source video path")
    ap.add_argument("-o", "--out", default="shots.json", help="output JSON path")
    ap.add_argument("--threshold", type=float, default=0.25, help="scene change threshold")
    ap.add_argument("--min-dur", type=float, default=1.0, help="drop shots shorter than this (s)")
    ap.add_argument("--dedupe", action="store_true",
                    help="merge near-identical consecutive shots (needs pillow + imagehash)")
    ap.add_argument("--dedupe-dist", type=int, default=6, help="dhash hamming distance to merge")
    ap.add_argument("--keep-work", action="store_true", help="keep intermediate scenes.txt")
    args = ap.parse_args()

    if not shutil.which("ffmpeg"):
        print("ERROR: ffmpeg not found in PATH", file=sys.stderr)
        return 2
    if not os.path.exists(args.video):
        print(f"ERROR: no such file: {args.video}", file=sys.stderr)
        return 2

    workdir = tempfile.mkdtemp(prefix="shots_")
    try:
        duration = probe_duration(args.video)
        fps = probe_fps(args.video)
        print(f"[S1] duration={duration:.1f}s fps={fps:g} threshold={args.threshold}")
        cuts = detect_scene_times(args.video, args.threshold, workdir)
        print(f"[S1] raw cut points: {len(cuts)}")
        shots = build_intervals(cuts, duration, args.min_dur)
        print(f"[S1] shots after min-dur filter: {len(shots)}")

        if args.dedupe:
            merged = 0
            prev_hash = None
            for i, shot in enumerate(shots):
                shot["id"] = f"S{i + 1:04d}"
                shot["still"] = False
                if prev_hash is not None:
                    h = dhash_at(args.video, shot["start"] + shot["dur"] / 2, workdir)
                    if h is not None and (h - prev_hash) <= args.dedupe_dist:
                        shot["still"] = True
                        merged += 1
                    if h is not None:
                        prev_hash = h
                else:
                    prev_hash = dhash_at(args.video, shot["start"] + shot["dur"] / 2, workdir)
            print(f"[S1] dedupe: {merged} shots flagged as still/duplicate")
        else:
            for i, shot in enumerate(shots):
                shot["id"] = f"S{i + 1:04d}"
                shot["still"] = False

        payload = {
            "video": os.path.abspath(args.video),
            "threshold": args.threshold,
            "duration": round(duration, 3),
            "fps": fps,
            "shots": shots,
        }
        os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)

        usable = sum(1 for s in shots if not s["still"])
        print(f"[S1] wrote {args.out}  shots={len(shots)} usable={usable}")
        if len(shots) > 3000:
            print("[S1] WARN: >3000 shots — raise --threshold to 0.35")
        elif len(shots) < 500 and duration > 600:
            print("[S1] WARN: <500 shots — lower --threshold to 0.15")
        return 0
    finally:
        if args.keep_work:
            print(f"[S1] workdir kept: {workdir}")
        else:
            shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())