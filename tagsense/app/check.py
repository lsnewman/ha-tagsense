"""Offline check over a folder of frames.

    python -m app.check DIR [--crop x1,y1,x2,y2] [--tag-id 5] [--save-annotated OUT]

Folder names are used as labels: a hit under absent/ is flagged PHANTOM, a miss
under present/ is MISSED, and a valid frame under smear/ or corrupt/ is NOT-REJECTED.
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

from .analysis import analyse
from .detector import DEFAULT_CROP, DEFAULT_MAX_ASPECT, Detector, annotate, validate_crop
from .sanity import DEFAULT_MIN_H, DEFAULT_MIN_RATIO

IMAGE_EXTS = {".jpg", ".jpeg", ".png"}
BAD_FOLDERS = {"smear", "corrupt"}


def parse_crop(s: str):
    parts = [float(p) for p in s.split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("crop must be x1,y1,x2,y2")
    return validate_crop(parts)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("dir", type=Path)
    ap.add_argument("--crop", type=parse_crop, default=DEFAULT_CROP)
    ap.add_argument("--tag-id", type=int, default=5)
    ap.add_argument("--max-aspect", type=float, default=DEFAULT_MAX_ASPECT,
                    help="shape gate: reject target decodes above this edge ratio (0 = off)")
    ap.add_argument("--min-ratio", type=float, default=DEFAULT_MIN_RATIO)
    ap.add_argument("--min-h", type=float, default=DEFAULT_MIN_H)
    ap.add_argument("--save-annotated", type=Path, metavar="OUT")
    a = ap.parse_args(argv)

    files = sorted(p for p in a.dir.rglob("*") if p.suffix.lower() in IMAGE_EXTS
                   and not p.name.endswith(".annotated.jpg"))
    if not files:
        print(f"no images under {a.dir}", file=sys.stderr)
        return 2
    if a.save_annotated:
        a.save_annotated.mkdir(parents=True, exist_ok=True)

    detector = Detector(a.tag_id, a.max_aspect)
    ratios = defaultdict(list)
    problems = 0
    print(f"crop={a.crop} tag_id={a.tag_id} min_ratio={a.min_ratio} min_h={a.min_h}")
    for f in files:
        label = f.parent.name
        r = analyse(f.read_bytes(), detector, a.crop, a.min_ratio, a.min_h)
        c, d = r.check, r.det
        ratios[label].append(c.ratio)
        flag = ""
        if label == "absent" and r.hit:
            flag = "PHANTOM"
        elif label == "present" and not r.hit:
            flag = "MISSED"
        elif label in BAD_FOLDERS and r.valid:
            flag = "NOT-REJECTED"
        problems += bool(flag)
        if d is None:
            print(f"{label:8} {f.name:45} {c.reason:13} {flag}")
            continue
        print(f"{label:8} {f.name:45} {'valid' if r.valid else 'INVALID':7} {c.reason:8} "
              f"v={c.v:5.2f} h={c.h:5.2f} r={c.ratio:4.2f} "
              f"found={'Y' if d.found else 'n'} "
              f"size={(f'{d.size_px:5.1f}' if d.found else '    -')} "
              f"ctr={(f'({d.centre_norm[0]:.3f},{d.centre_norm[1]:.3f})' if d.found else '-'):15} "
              f"mean={d.mean:5.1f} std={d.std:4.1f} {d.ms:5.1f}ms"
              f"{''.join('  [' + o.describe() + ']' for o in d.others)} {flag}")
        if a.save_annotated:
            (a.save_annotated / f"{label}_{f.stem}.annotated.jpg").write_bytes(annotate(c.bgr, d))

    print("\nsanity ratio by folder:")
    for label, rs in sorted(ratios.items()):
        print(f"  {label:8} n={len(rs):3} min={min(rs):.3f} max={max(rs):.3f}")
    print(f"\n{problems} flagged frame(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
