#!/usr/bin/env python3
"""S7: cut clips from an EDL and concatenate them into a single video.

Usage:
    python cut_edl.py match.mp4 work/edl.json -o work/clips --accurate

EDL format (edl.json) — in/out accept "HH:MM:SS.mmm" or plain seconds:
    [{"idx":1,"in":"00:23:41.200","out":"00:23:48.900","scene":"边路突破","text":"他从左路内切"}]

Flags:
    --accurate   -ss after -i (frame-exact, re-encodes)   [default]
    --fast       -ss before -i + -c copy (keyframe-aligned, ~0.5s drift, instant)
    --mute       drop audio (for voice-over assembly)
    --no-concat  only produce clips/, skip final.mp4
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys


def to_seconds(value) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if re.fullmatch(r"[0-9.]+", text):
        return float(text)
    parts = text.split(":")
    if len(parts) == 3:
        h, m, s = parts
        return int(h) * 3600 + int(m) * 60 + float(s)
    if len(parts) == 2:
        m, s = parts
        return int(m) * 60 + float(s)
    raise ValueError(f"bad time value: {value!r}")


def build_cmd(video: str, item: dict, out: str, accurate: bool, mute: bool) -> list[str]:
    start = to_seconds(item["in"])
    end = to_seconds(item["out"])
    dur = end - start
    audio = ["-an"] if mute else ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]
    if accurate:
        cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-i", video,
               "-ss", f"{start:.3f}", "-t", f"{dur:.3f}"]
    else:
        cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-ss", f"{start:.3f}",
               "-i", video, "-t", f"{dur:.3f}"]
    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-pix_fmt", "yuv420p", "-r", "30", "-vsync", "cfr",
            "-avoid_negative_ts", "make_zero", *audio, "-movflags", "+faststart", out]
    return cmd


def main() -> int:
    ap = argparse.ArgumentParser(description="Cut EDL clips and concatenate.")
    ap.add_argument("video")
    ap.add_argument("edl")
    ap.add_argument("-o", "--outdir", default="clips")
    ap.add_argument("--accurate", dest="accurate", action="store_true", default=True)
    ap.add_argument("--fast", dest="accurate", action="store_false")
    ap.add_argument("--mute", action="store_true")
    ap.add_argument("--no-concat", action="store_true")
    ap.add_argument("--csv", default=None, help="also write a review CSV here")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    with open(args.edl, encoding="utf-8") as fh:
        edl = json.load(fh)
    if not isinstance(edl, list) or not edl:
        print("ERROR: EDL must be a non-empty JSON array", file=sys.stderr)
        return 2

    os.makedirs(args.outdir, exist_ok=True)
    clips: list[str] = []
    rows = []

    for n, item in enumerate(edl, start=1):
        try:
            start, end = to_seconds(item["in"]), to_seconds(item["out"])
        except (KeyError, ValueError) as exc:
            print(f"WARN: item {n} skipped ({exc})", file=sys.stderr)
            continue
        if end <= start:
            print(f"WARN: item {n} has out <= in, skipped", file=sys.stderr)
            continue
        out = os.path.join(args.outdir, f"clip_{n:03d}.mp4")
        cmd = build_cmd(args.video, item, out, args.accurate, args.mute)
        rows.append({
            "idx": item.get("idx", n), "in": item["in"], "out": item["out"],
            "dur": round(end - start, 3), "scene": item.get("scene", ""),
            "text": item.get("text", ""), "file": os.path.basename(out),
        })
        if args.dry_run:
            print(" ".join(cmd))
            continue
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            print(f"ERROR: clip {n} failed\n{r.stderr[-800:]}", file=sys.stderr)
            continue
        clips.append(out)
        print(f"[S7] {n}/{len(edl)} -> {out} ({end - start:.2f}s)")

    if args.csv:
        import csv
        with open(args.csv, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=["idx", "in", "out", "dur", "scene", "text", "file"])
            w.writeheader()
            w.writerows(rows)
        print(f"[S7] csv -> {args.csv}")

    if args.dry_run:
        return 0

    if not clips:
        print("ERROR: no clips produced", file=sys.stderr)
        return 1

    if args.no_concat:
        print(f"[S7] {len(clips)} clips in {args.outdir}/ (concat skipped)")
        return 0

    listfile = os.path.join(args.outdir, "list.txt")
    with open(listfile, "w", encoding="utf-8") as fh:
        for c in clips:
            fh.write(f"file '{os.path.abspath(c)}'\n")
    final = os.path.join(args.outdir, "final.mp4")
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-f", "concat", "-safe", "0",
           "-i", listfile, "-c", "copy", final]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-f", "concat", "-safe", "0",
               "-i", listfile, "-c:v", "libx264", "-crf", "18", "-c:a", "aac", final]
        r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"ERROR: concat failed\n{r.stderr[-800:]}", file=sys.stderr)
        return 1

    total = sum(to_seconds(i["out"]) - to_seconds(i["in"]) for i in edl
                if "in" in i and "out" in i)
    print(f"[S7] final -> {final}  clips={len(clips)}  total≈{total:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())