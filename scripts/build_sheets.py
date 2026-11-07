#!/usr/bin/env python3
"""S3: extract one frame per shot and tile them into labelled contact sheets.

Usage:
    python build_sheets.py match.mp4 work/shots.json -o work/sheets --grid 4 --size 512

Each sheet holds grid*grid shots with the shot id burned into the top-left corner,
so the vision model can echo ids back in its JSON output.

Outputs:
    sheets/sheet_0001.jpg ...
    sheet_index.json   {"sheets":[{"sheet":"sheet_0001.jpg","shots":["S0001",...]}]}

Note: frames are grabbed with keyframe-accurate seek (-ss before -i). Thumbnails may
be a second off — irrelevant for scene classification, much faster than frame-exact.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor


def grab(video: str, t: float, out: str, size: int) -> bool:
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-ss", f"{t:.3f}", "-i", video,
           "-frames:v", "1", "-vf", f"scale={size}:-2", "-q:v", "3", out]
    r = subprocess.run(cmd, capture_output=True)
    return r.returncode == 0 and os.path.exists(out)


def main() -> int:
    ap = argparse.ArgumentParser(description="Build labelled contact sheets from shots.json.")
    ap.add_argument("video")
    ap.add_argument("shots", help="shots.json produced by split_shots.py")
    ap.add_argument("-o", "--outdir", default="sheets")
    ap.add_argument("--grid", type=int, default=4, help="grid*grid shots per sheet")
    ap.add_argument("--size", type=int, default=512, help="cell long edge in px")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--skip-still", action="store_true", help="skip shots flagged still=true")
    args = ap.parse_args()

    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("ERROR: pillow required (pip install pillow)", file=sys.stderr)
        return 2

    with open(args.shots, encoding="utf-8") as fh:
        data = json.load(fh)
    shots = [s for s in data["shots"] if not (args.skip_still and s.get("still"))]
    if not shots:
        print("ERROR: no shots to render", file=sys.stderr)
        return 1

    os.makedirs(args.outdir, exist_ok=True)
    tmp = os.path.join(args.outdir, "_frames")
    os.makedirs(tmp, exist_ok=True)

    # 1) grab frames (parallel, keyframe seek is fast enough for thumbnails)
    def job(shot):
        out = os.path.join(tmp, f"{shot['id']}.jpg")
        mid = shot["start"] + shot["dur"] / 2
        return shot["id"], grab(args.video, mid, out, args.size)

    ok, fail = 0, []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for sid, good in pool.map(job, shots):
            if good:
                ok += 1
            else:
                fail.append(sid)
    print(f"[S3] frames extracted: {ok}/{len(shots)}" + (f" failed={len(fail)}" if fail else ""))

    # 2) tile
    font = None
    for cand in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                 "C:/Windows/Fonts/arialbd.ttf"):
        if os.path.exists(cand):
            font = ImageFont.truetype(cand, max(14, args.size // 18))
            break
    if font is None:
        font = ImageFont.load_default()

    per = args.grid * args.grid
    index = []
    sheet_no = 0
    for start in range(0, len(shots), per):
        chunk = shots[start:start + per]
        sheet_no += 1
        cell_w = args.size
        cell_h = int(args.size * 9 / 16)
        canvas = Image.new("RGB", (cell_w * args.grid, cell_h * args.grid), (16, 16, 16))
        draw = ImageDraw.Draw(canvas)
        placed = []
        for i, shot in enumerate(chunk):
            path = os.path.join(tmp, f"{shot['id']}.jpg")
            if not os.path.exists(path):
                continue
            img = Image.open(path).convert("RGB").resize((cell_w, cell_h))
            x = (i % args.grid) * cell_w
            y = (i // args.grid) * cell_h
            canvas.paste(img, (x, y))
            label = shot["id"]
            bbox = draw.textbbox((0, 0), label, font=font)
            pad = 4
            draw.rectangle([x, y, x + bbox[2] - bbox[0] + pad * 2, y + bbox[3] - bbox[1] + pad * 2],
                           fill=(0, 0, 0))
            draw.text((x + pad, y + pad), label, fill=(255, 235, 60), font=font)
            placed.append(shot["id"])
        name = f"sheet_{sheet_no:04d}.jpg"
        canvas.save(os.path.join(args.outdir, name), quality=88)
        index.append({"sheet": name, "shots": placed})

    with open(os.path.join(args.outdir, "sheet_index.json"), "w", encoding="utf-8") as fh:
        json.dump({"grid": args.grid, "size": args.size, "sheets": index}, fh,
                  ensure_ascii=False, indent=2)

    for f in os.listdir(tmp):
        try:
            os.remove(os.path.join(tmp, f))
        except OSError:
            pass
    os.rmdir(tmp)

    print(f"[S3] wrote {len(index)} sheets -> {args.outdir}/  ({len(shots)} shots)")
    print(f"[S3] next: hand these to the vision model with the tagging prompt "
          f"in references/prompts.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())