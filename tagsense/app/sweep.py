"""Per-family detector sweep: decode margin, phantoms and parameter ablation.

    python -m app.sweep DIR [--families tag16h5,tag36h11] [--crop x1,y1,x2,y2]
                            [--tag-id 5] [--textures 300] [--ablation] [--full-frame]

DIR is laid out like app.check expects (present/, absent/, smear/). Real frames
are never committed. Sections:

  synthetic   each family's tag drawn at several sizes over the real backgrounds,
              plain, blurred and IR-like (low contrast + noise).
  transplant  each present/ frame's real tag16h5 (--tag-id) is found, then the
              candidate family's tag is warped onto the same corners, i.e. the
              same physical size and angle as the real tag, and shrunk about its
              centre to find where decoding gives out.
  phantoms    decodes of any id in absent/ and smear/ crops, the present/ crops
              away from the real tag, and random textures (gravel-like noise).
  ablation    (--ablation) each tuned parameter reset to the OpenCV default, one
              at a time, against the transplant and texture results.

    python -m app.sweep --qr [DIR]

  qr          QR decode rate by module size (px per QR module in the full
              frame), plain, blurred, low-contrast and with phone-screen glare,
              plus decode time; and real frames in DIR/qr if present.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import cv2
import cv2.aruco as aruco
import numpy as np

from .check import IMAGE_EXTS, parse_crop
from .detector import (DEFAULT_CROP, FAMILIES, Detector, build_params, crop_pixels,
                       family_info)
from .sanity import decode_jpeg

SIZES = (16, 20, 24, 28, 32, 40, 50, 64)        # longer diagonal, px in a 1080p frame
SCALES = (1.0, 0.85, 0.7, 0.6, 0.5, 0.4)          # transplant shrink factors
CONDITIONS = ("plain", "blur", "ir")
NO_TARGET = -1                                    # every decode lands in det.others

# The tuned fields of build_params() and what OpenCV ships with.
ABLATION = {
    "perspectiveRemovePixelPerCell": 4,
    "perspectiveRemoveIgnoredMarginPerCell": 0.13,
    "maxErroneousBitsInBorderRate": 0.35,
    "cornerRefinementMethod": aruco.CORNER_REFINE_NONE,
    "aprilTagQuadDecimate": 0.0,
    "cornerRefinementMaxIterations": 30,
}


def load_frames(root: Path, label: str) -> list[tuple[str, np.ndarray]]:
    d = root / label
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.rglob("*")):
        if p.suffix.lower() in IMAGE_EXTS and not p.name.endswith(".annotated.jpg"):
            img = decode_jpeg(p.read_bytes())
            if img is not None:
                out.append((p.name, img))
    return out


def tag_tile(family: str, tag_id: int, cell_px: int = 20) -> tuple[np.ndarray, int]:
    """Marker with a one-cell white quiet zone (as printed); returns (tile, border px)."""
    fi = family_info(family)
    marker = aruco.generateImageMarker(fi.dictionary, tag_id, fi.cells * cell_px)
    return cv2.copyMakeBorder(marker, cell_px, cell_px, cell_px, cell_px,
                              cv2.BORDER_CONSTANT, value=255), cell_px


def paste(frame: np.ndarray, tile: np.ndarray, border: int, quad: np.ndarray) -> np.ndarray:
    """Warp `tile` so its marker (inside `border`) lands on `quad` (4x2, TL TR BR BL)."""
    s = tile.shape[0]
    src = np.float32([[border, border], [s - border, border], [s - border, s - border],
                      [border, s - border]])
    h, w = frame.shape[:2]
    H = cv2.getPerspectiveTransform(src, quad.astype(np.float32))
    warped = cv2.warpPerspective(cv2.cvtColor(tile, cv2.COLOR_GRAY2BGR), H, (w, h),
                                 flags=cv2.INTER_AREA)
    mask = cv2.warpPerspective(np.full((s, s), 255, np.uint8), H, (w, h))
    out = frame.copy()
    out[mask > 0] = warped[mask > 0]
    return out


def degrade(img: np.ndarray, condition: str, rng: np.random.Generator) -> np.ndarray:
    if condition == "blur":
        return cv2.GaussianBlur(img, (0, 0), 1.2)
    if condition == "ir":           # night: ~1/5 of the daytime contrast, sensor noise
        g = img.astype(np.float32)
        g = (g - g.mean()) * 0.2 + 60 + rng.normal(0, 4, g.shape)
        return g.clip(0, 255).astype(np.uint8)
    return img


def lid_quad(centre, diag: float) -> np.ndarray:
    """A tag lying flat, seen from above at an angle (as on the bin lid)."""
    cx, cy = centre
    hw, hh = diag * 0.42, diag * 0.26
    return np.float32([[cx - hw * 0.9, cy - hh], [cx + hw * 0.9, cy - hh],
                       [cx + hw, cy + hh], [cx - hw, cy + hh]])


def shrink(quad: np.ndarray, k: float) -> np.ndarray:
    c = quad.mean(axis=0)
    return (c + (quad - c) * k).astype(np.float32)


def table(title: str, cols: list[str], rows: list[tuple[str, list]]):
    print(f"\n## {title}")
    w = max(12, *(len(r[0]) for r in rows)) if rows else 12
    print(f"{'':{w}} " + " ".join(f"{c:>8}" for c in cols))
    for name, vals in rows:
        print(f"{name:{w}} " + " ".join(f"{v:>8}" for v in vals))


# --- sections ------------------------------------------------------------------

def synthetic(families, backgrounds, crop, per_cell: int, rng) -> dict:
    """Decode rate by size and condition, tags placed at random inside the crop."""
    res = {}
    for fam in families:
        n = family_info(fam).id_count
        for cond in CONDITIONS:
            row = []
            for size in SIZES:
                ok = 0
                for i in range(per_cell):
                    _, bg = backgrounds[i % len(backgrounds)]
                    px1, py1, px2, py2 = crop_pixels(bg.shape, crop)
                    m = size
                    centre = (rng.uniform(px1 + m, max(px1 + m + 1, px2 - m)),
                              rng.uniform(py1 + m, max(py1 + m + 1, py2 - m)))
                    tag = int(rng.integers(n))
                    tile, b = tag_tile(fam, tag)
                    img = degrade(paste(bg, tile, b, lid_quad(centre, size)), cond, rng)
                    ok += Detector(tag, max_aspect=0, family=fam).detect(img, crop).found
                row.append(f"{ok}/{per_cell}")
            res[(fam, cond)] = row
    return res


def real_tags(present, crop, tag_id: int) -> list[tuple[str, np.ndarray, np.ndarray]]:
    det = Detector(tag_id, max_aspect=0)
    out = []
    for name, img in present:
        d = det.detect(img, crop)
        if d.found:
            out.append((name, img, d.corners))
    return out


def transplant(families, reals, crop, tag_id: int, params=None,
               conditions=("plain", "blur")) -> dict:
    """Decode rate of each family's tag warped onto the real tag's corners, shrunk.
    The pasted tag is a crisp render, so "plain" is optimistic; "blur" softens the
    pasted region (sigma 1.2 px) as camera optics and compression would."""
    res = {}
    for fam in families:
        n = family_info(fam).id_count
        tid = min(tag_id, n - 1)
        tile, b = tag_tile(fam, tid)
        det = Detector(tid, max_aspect=0, family=fam, params=params)
        for cond in conditions:
            row = []
            for k in SCALES:
                ok = 0
                for _, img, c in reals:
                    quad = shrink(c, k)
                    out = paste(img, tile, b, quad)
                    if cond == "blur":
                        x, y, w, h = cv2.boundingRect(shrink(quad, 1.6).astype(np.int32))
                        out[y:y + h, x:x + w] = cv2.GaussianBlur(out[y:y + h, x:x + w], (0, 0), 1.2)
                    ok += det.detect(out, crop).found
                row.append(f"{ok}/{len(reals)}")
            res[f"{fam} {cond}" if len(conditions) > 1 else fam] = row
    return res


def textures(count: int, seed: int = 7) -> list[np.ndarray]:
    """Gravel-like random textures: blurred noise at mixed scales, thresholded softly."""
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(count):
        img = np.zeros((360, 480), np.float32)
        for scale in (2, 4, 8):
            n = rng.normal(0, 1, (360 // scale + 1, 480 // scale + 1)).astype(np.float32)
            img += cv2.resize(n, (480, 360), interpolation=cv2.INTER_NEAREST)[:360, :480] / scale
        img = cv2.GaussianBlur(img, (0, 0), rng.uniform(0.5, 1.5))
        img = 1 / (1 + np.exp(-img * rng.uniform(2, 6)))
        out.append(cv2.cvtColor((img * 255).astype(np.uint8), cv2.COLOR_GRAY2BGR))
    return out


def phantoms(families, sets: dict[str, list], crop, avoid: dict[str, np.ndarray],
             params=None) -> dict:
    """Count decodes of any id. `avoid` maps a frame name to the real tag's corners
    (decodes centred inside its bounding box are the real tag, not phantoms)."""
    res = {}
    for fam in families:
        det = Detector(NO_TARGET, max_aspect=0, family=fam, params=params)
        row = []
        for label, frames in sets.items():
            count = 0
            use_crop = (0, 0, 1, 1) if label == "textures" else crop
            for name, img in frames:
                for o in det.detect(img, use_crop).others:
                    if name in avoid:
                        x1, y1 = avoid[name].min(axis=0) - 5
                        x2, y2 = avoid[name].max(axis=0) + 5
                        cx, cy = o.corners.mean(axis=0)
                        if x1 <= cx <= x2 and y1 <= cy <= y2:
                            continue
                    count += 1
            row.append(str(count))
        res[fam] = row
    return res


def ablated_params(field: str) -> aruco.DetectorParameters:
    p = build_params()
    setattr(p, field, ABLATION[field])
    return p


QR_MODULE_PX = (2.0, 2.5, 3.0, 4.0, 5.0)
QR_PAYLOADS = {"25 chars": "TS1RAB12CD34EF56GH78JK2Q3", "45 chars": "TS1SAB12CD34X7Q2K9M" + "Z" * 26}
QR_CONDITIONS = ("plain", "blur", "dim", "glare", "bloom")


def qr_scene(payload: str, module_px: float, cond: str, rng) -> np.ndarray:
    """A 1280x720 frame with a phone-held QR code, tilted a little."""
    frame = rng.normal(110, 20, (720, 1280, 3)).clip(0, 255).astype(np.uint8)
    q = cv2.QRCodeEncoder.create().encode(payload)
    side = int(q.shape[0] * module_px)
    img = cv2.cvtColor(cv2.resize(q, (side, side), interpolation=cv2.INTER_AREA), cv2.COLOR_GRAY2BGR)
    if cond == "bloom":             # an over-bright screen: white bleeds into the dark modules
        img = cv2.dilate(img, np.ones((max(2, int(module_px * 0.45)),) * 2, np.uint8))
    if cond == "glare":             # a bright diagonal band, as a screen reflection
        yy, xx = np.mgrid[0:side, 0:side]
        band = np.exp(-((xx - yy) / (side * 0.12)) ** 2)[..., None] * 160
        img = np.clip(img.astype(np.float32) * 0.85 + band, 0, 255).astype(np.uint8)
    x, y, j = rng.uniform(300, 1280 - side - 300), rng.uniform(100, 720 - side - 100), side * 0.1
    src = np.float32([[0, 0], [side, 0], [side, side], [0, side]])
    dst = np.float32([[x + rng.uniform(0, j), y], [x + side, y + rng.uniform(0, j)],
                      [x + side - rng.uniform(0, j), y + side], [x, y + side - rng.uniform(0, j)]])
    H = cv2.getPerspectiveTransform(src, dst)
    warped = cv2.warpPerspective(img, H, (1280, 720))
    mask = cv2.warpPerspective(np.full((side, side), 255, np.uint8), H, (1280, 720))
    frame[mask > 0] = warped[mask > 0]
    if cond == "blur":
        frame = cv2.GaussianBlur(frame, (0, 0), 1.0)
    elif cond == "dim":
        frame = np.clip((frame.astype(np.float32) - 110) * 0.35 + 60
                        + rng.normal(0, 4, frame.shape), 0, 255).astype(np.uint8)
    return frame


def qr_sweep(root: Path | None, trials: int) -> int:
    from .access.qr import QrDecoder
    rng = np.random.default_rng(3)
    decs = {"zxing": QrDecoder(("zxing",)), "opencv": QrDecoder(("classic", "aruco"))}
    print(f"# QR sweep, {trials} trials per cell (the same frames for each decoder), "
          f"OpenCV {cv2.__version__}")
    for label, payload in QR_PAYLOADS.items():
        q = cv2.QRCodeEncoder.create().encode(payload)
        hits = {(d, c, m): 0 for d in decs for c in QR_CONDITIONS for m in QR_MODULE_PX}
        times = {d: [] for d in decs}
        for cond in QR_CONDITIONS:
            for m in QR_MODULE_PX:
                for _ in range(trials):
                    img = qr_scene(payload, m, cond, rng)
                    for d, dec in decs.items():
                        t = time.perf_counter()
                        r = dec.decode(img)
                        times[d].append((time.perf_counter() - t) * 1000)
                        hits[(d, cond, m)] += bool(r and r.text == payload)
        table(f"QR {label} ({q.shape[0]} modules with quiet zone): decodes by px per module",
              [f"{m:g}px" for m in QR_MODULE_PX],
              [(f"{d} {c}", [f"{hits[(d, c, m)]}/{trials}" for m in QR_MODULE_PX])
               for c in QR_CONDITIONS for d in decs])
        for d, ts in times.items():
            print(f"{d}: median {np.median(ts):.0f} ms, max {max(ts):.0f} ms per 1280x720 frame "
                  "(reads and misses)")
    real = load_frames(root, "qr") if root else []
    if real:
        for d, dec in decs.items():
            print(f"real frames in {root}/qr, {d}: "
                  f"{sum(bool(dec.decode(img)) for _, img in real)}/{len(real)} read")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("dir", type=Path, nargs="?")
    ap.add_argument("--qr", action="store_true", help="QR decoding instead of tag families")
    ap.add_argument("--qr-trials", type=int, default=10)
    ap.add_argument("--families", default=",".join(FAMILIES))
    ap.add_argument("--crop", type=parse_crop, default=DEFAULT_CROP)
    ap.add_argument("--tag-id", type=int, default=5, help="the real tag16h5 id in present/")
    ap.add_argument("--per-cell", type=int, default=20, help="synthetic trials per size")
    ap.add_argument("--textures", type=int, default=300)
    ap.add_argument("--ablation", action="store_true")
    ap.add_argument("--full-frame", action="store_true",
                    help="also count phantoms over whole frames (slow)")
    a = ap.parse_args(argv)
    if a.qr:
        return qr_sweep(a.dir, a.qr_trials)
    if a.dir is None:
        ap.error("DIR is required (except with --qr)")
    families = [f.strip() for f in a.families.split(",") if f.strip()]
    for f in families:
        family_info(f)

    present, absent = load_frames(a.dir, "present"), load_frames(a.dir, "absent")
    smear = load_frames(a.dir, "smear") + load_frames(a.dir, "corrupt")
    if not present and not absent:
        print(f"no frames under {a.dir}/present or /absent", file=sys.stderr)
        return 2
    t0 = time.time()
    rng = np.random.default_rng(0)
    print(f"# TagSense detector sweep\nframes: present {len(present)}, absent {len(absent)}, "
          f"smear {len(smear)}; crop {a.crop}; OpenCV {cv2.__version__}")

    syn = synthetic(families, absent or present, a.crop, a.per_cell, rng)
    table("Synthetic: decodes by tag size (longer diagonal, px), tag on a lid",
          [f"{s}px" for s in SIZES], [(f"{f} {c}", v) for (f, c), v in syn.items()])

    reals = real_tags(present, a.crop, a.tag_id)
    avoid = {name: c for name, _, c in reals}
    if reals:
        sizes = [float(max(np.linalg.norm(c[0] - c[2]), np.linalg.norm(c[1] - c[3])))
                 for _, _, c in reals]
        print(f"\nreal tag{a.tag_id} found in {len(reals)}/{len(present)} present frames, "
              f"size {min(sizes):.0f}-{max(sizes):.0f}px")
        table("Transplant: the family's tag on the real tag's corners, shrunk by",
              [f"x{k:g}" for k in SCALES],
              list(transplant(families, reals, a.crop, a.tag_id).items()))

    tex = [(f"t{i}", t) for i, t in enumerate(textures(a.textures))]
    sets = {"absent": absent, "smear": smear, "present": present, "textures": tex}
    table("Phantoms: decodes of any id (real tag excluded)", list(sets),
          list(phantoms(families, sets, a.crop, avoid).items()))
    if a.full_frame:
        full = {"absent": absent, "present": present}
        table("Phantoms over whole frames", list(full),
              list(phantoms(families, full, (0, 0, 1, 1), avoid).items()))

    if a.ablation and reals:
        cols = [f"x{k:g}" for k in SCALES] + ["textures"]
        for fam in families:
            rows = []
            for field in ["(tuned)", *ABLATION]:
                params = build_params() if field == "(tuned)" else ablated_params(field)
                tr = transplant([fam], reals, a.crop, a.tag_id, params, ("blur",))[fam]
                ph = phantoms([fam], {"textures": tex}, a.crop, {}, params)[fam]
                rows.append((field, tr + ph))
            table(f"Ablation ({fam}, blurred transplant): one tuned parameter reset "
                  "to the OpenCV default",
                  cols, rows)
    print(f"\n{time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
