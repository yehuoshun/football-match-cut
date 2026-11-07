#!/usr/bin/env python3
"""S2: locate goals by watching the scoreboard region (pixel diff + optional OCR).

Usage:
    python score_events.py match.mp4 -o work/goals.json --rect 0.0,0.0,0.30,0.10 --fps 0.5

--rect is x,y,w,h as FRACTIONS of the frame (0~1). Check the crop once before batch
running: crop_frame.py is not needed, just use ffmpeg -vf crop and look at the jpg.

Two modes:
  * default      pixel-diff only -> "candidates" you review by hand (0 deps)
  * --ocr        OCR each candidate -> score series -> auto-detect goals
                 (needs paddleocr or pytesseract; results still need a human check)

Output (goals.json):
    {"video":..., "rect":[...], "fps":0.5,
     "candidates":[{"t":123.0,"diff":18.4,"score":"1 - 0"}],
     "goals":[{"t":123.0,"from":"0 - 0","to":"1 - 0"}]}
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

SCORE_RE = re.compile(r"(\d{1,2})\s*[-:：–—]\s*(\d{1,2})")


def extract_crops(video: str, fps: float, rect: tuple[float, float, float, float],
                  workdir: str, scale: float) -> list[str]:
    x, y, w, h = rect
    vf = (f"fps={fps},crop=iw*{w}:ih*{h}:iw*{x}:ih*{y},"
          f"scale=iw*{scale:g}:-2")
    pattern = os.path.join(workdir, "crop_%05d.jpg")
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-i", video, "-an",
         "-vf", vf, "-q:v", "3", pattern],
        check=True, capture_output=True,
    )
    return sorted(glob.glob(os.path.join(workdir, "crop_*.jpg")))


def load_ocr():
    try:
        from paddleocr import PaddleOCR  # type: ignore
        ocr = PaddleOCR(use_angle_cls=False, lang="en", show_log=False)

        def run(path: str) -> str:
            res = ocr.ocr(path, cls=False)
            if not res or not res[0]:
                return ""
            return " ".join(str(item[1][0]) for item in res[0])

        return run, "paddleocr"
    except Exception:
        pass
    try:
        import pytesseract  # type: ignore
        from PIL import Image

        def run(path: str) -> str:
            return pytesseract.image_to_string(Image.open(path), config="--psm 7")

        return run, "pytesseract"
    except Exception:
        return None, None


def parse_score(text: str) -> tuple[int, int] | None:
    m = SCORE_RE.search(text or "")
    if not m:
        return None
    a, b = int(m.group(1)), int(m.group(2))
    if a > 20 or b > 20:  # OCR garbage guard
        return None
    return a, b


def main() -> int:
    ap = argparse.ArgumentParser(description="Detect goal moments from the scoreboard region.")
    ap.add_argument("video")
    ap.add_argument("-o", "--out", default="goals.json")
    ap.add_argument("--rect", default="0.0,0.0,0.30,0.10",
                    help="x,y,w,h as fractions of frame (default top-left 30%%x10%%)")
    ap.add_argument("--fps", type=float, default=0.5, help="sampling rate of the crop")
    ap.add_argument("--scale", type=float, default=3.0, help="upscale factor before OCR")
    ap.add_argument("--diff-threshold", type=float, default=6.0,
                    help="mean abs pixel diff to flag a change candidate")
    ap.add_argument("--merge-window", type=float, default=8.0,
                    help="merge candidates within N seconds")
    ap.add_argument("--ocr", action="store_true", help="run OCR on candidates")
    args = ap.parse_args()

    if not os.path.exists(args.video):
        print(f"ERROR: no such file: {args.video}", file=sys.stderr)
        return 2
    try:
        rect = tuple(float(v) for v in args.rect.split(","))
        assert len(rect) == 4
    except Exception:
        print("ERROR: --rect must be x,y,w,h fractions, e.g. 0.0,0.0,0.30,0.10", file=sys.stderr)
        return 2

    import numpy as np
    from PIL import Image

    workdir = tempfile.mkdtemp(prefix="score_")
    try:
        files = extract_crops(args.video, args.fps, rect, workdir, args.scale)
        if not files:
            print("ERROR: no frames extracted — check --rect", file=sys.stderr)
            return 1
        print(f"[S2] sampled {len(files)} crop frames at {args.fps} fps")

        prev = None
        cands: list[tuple[float, float, str]] = []
        for i, path in enumerate(files):
            img = Image.open(path).convert("L").resize((160, 60))
            arr = np.asarray(img, dtype=np.int16)
            if prev is not None:
                diff = float(np.abs(arr - prev).mean())
                if diff > args.diff_threshold:
                    cands.append((i / args.fps, diff, path))
            prev = arr

        # merge nearby candidates
        merged: list[tuple[float, float, str]] = []
        for t, d, p in cands:
            if merged and t - merged[-1][0] <= args.merge_window:
                if d > merged[-1][1]:
                    merged[-1] = (merged[-1][0], d, p)
                continue
            merged.append((t, d, p))
        print(f"[S2] change candidates: {len(cands)} (merged to {len(merged)})")

        ocr_run, engine = (load_ocr() if args.ocr else (None, None))
        if args.ocr and ocr_run is None:
            print("[S2] WARN: no OCR backend (pip install paddleocr / pytesseract)", file=sys.stderr)

        out_cands = []
        series: list[tuple[float, tuple[int, int]]] = []
        for t, d, path in merged:
            item = {"t": round(t, 2), "diff": round(d, 2)}
            if ocr_run:
                text = ocr_run(path).strip()
                score = parse_score(text)
                item["ocr"] = text[:40]
                if score:
                    item["score"] = f"{score[0]} - {score[1]}"
                    series.append((t, score))
            out_cands.append(item)

        goals = []
        for prev_item, item in zip(series, series[1:]):
            if item[1] != prev_item[1]:
                goals.append({
                    "t": round(item[0], 2),
                    "from": f"{prev_item[1][0]} - {prev_item[1][1]}",
                    "to": f"{item[1][0]} - {item[1][1]}",
                })

        payload = {
            "video": os.path.abspath(args.video),
            "rect": list(rect),
            "fps": args.fps,
            "ocr": engine,
            "candidates": out_cands,
            "goals": goals,
        }
        os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        print(f"[S2] wrote {args.out}  candidates={len(out_cands)} goals={len(goals)}")
        if not args.ocr:
            print("[S2] hint: add --ocr for automatic goal detection, or review candidates manually")
        if goals:
            print("[S2] remember: verify against the actual score — offside goals get disallowed")
        return 0
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())