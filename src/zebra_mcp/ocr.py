"""PP-OCRv4 det+rec inference (ONNX), vendored so photo->ZPL has no external checkout.

Same post-processing as PaddleOCR itself (DB threshold 0.3, box score 0.5,
unclip 1.5 via pyclipper, CTC decode). The unclip step is not cosmetic: a
centroid-scaled approximation reads 'NANJING' as 'NAN-ING' on a 154 px wide
line because the crop comes out 4 px shorter.

Model files are NOT vendored (16 MB of binary): point ZEBRA_OCR_MODELS at the
directory holding ch_PP-OCRv4_det_infer.onnx / ch_PP-OCRv4_rec_infer.onnx /
ppocr_keys_v1.txt, or drop them in <repo>/data/ocr/.
"""
from __future__ import annotations

import math
import os
from pathlib import Path

import cv2
import numpy as np

__all__ = ["models_dir", "available", "load", "detect", "recognize", "run"]

DET_NAME = "ch_PP-OCRv4_det_infer.onnx"
REC_NAME = "ch_PP-OCRv4_rec_infer.onnx"
KEYS_NAME = "ppocr_keys_v1.txt"

_SEARCH = [
    os.environ.get("ZEBRA_OCR_MODELS", ""),
    str(Path(__file__).resolve().parents[2] / "data" / "ocr"),
    str(Path.home() / "models" / "paddleocr"),
]

DET_MEAN = np.array([0.485, 0.456, 0.406], np.float32)
DET_STD = np.array([0.229, 0.224, 0.225], np.float32)

_CHARS: list[str] | None = None
_SESSIONS: tuple | None = None


def models_dir() -> Path | None:
    for cand in _SEARCH:
        if not cand:
            continue
        p = Path(cand)
        if all((p / n).exists() for n in (DET_NAME, REC_NAME, KEYS_NAME)):
            return p
    return None


def available() -> dict:
    d = models_dir()
    if not d:
        return {"ok": False, "error": "PP-OCRv4 models not found",
                "hint": f"set ZEBRA_OCR_MODELS to a dir with {DET_NAME}, {REC_NAME}, {KEYS_NAME}"}
    try:
        import onnxruntime  # noqa: F401
    except Exception as exc:
        return {"ok": False, "error": f"onnxruntime missing: {exc}"}
    return {"ok": True, "models": str(d)}


def _chars() -> list[str]:
    global _CHARS
    if _CHARS is None:
        d = models_dir()
        if not d:
            raise RuntimeError("PP-OCRv4 models not found")
        keys = [k for k in (d / KEYS_NAME).read_text(encoding="utf-8").split("\n") if k != ""]
        _CHARS = ["blank"] + keys + [" "]
    return _CHARS


def load():
    """(det, rec) sessions, created once per process."""
    global _SESSIONS
    if _SESSIONS is None:
        import onnxruntime as ort
        d = models_dir()
        if not d:
            raise RuntimeError("PP-OCRv4 models not found")
        so = ort.SessionOptions()
        so.intra_op_num_threads = 4
        _SESSIONS = (ort.InferenceSession(str(d / DET_NAME), sess_options=so),
                     ort.InferenceSession(str(d / REC_NAME), sess_options=so))
    return _SESSIONS


# ---------------------------------------------------------------- DB detection

def _resize_to_limit(img, limit=736, stride=32):
    h, w = img.shape[:2]
    ratio = limit / min(h, w) if min(h, w) < limit else 1.0
    rh = max(int(round(h * ratio / stride) * stride), stride)
    rw = max(int(round(w * ratio / stride) * stride), stride)
    resized = cv2.resize(img, (rw, rh)).astype(np.float32)
    pad = np.zeros((rh, rw, 3), np.float32)
    pad[: rh, : rw] = resized
    return pad, ratio


def _mini_boxes(contour):
    bb = cv2.minAreaRect(contour)
    pts = sorted(list(cv2.boxPoints(bb)), key=lambda p: p[0])
    i1, i4 = (0, 1) if pts[1][1] > pts[0][1] else (1, 0)
    i2, i3 = (2, 3) if pts[3][1] > pts[2][1] else (3, 2)
    return np.array([pts[i1], pts[i2], pts[i3], pts[i4]], np.float32), min(bb[1])


def _box_score(prob, box_points):
    h, w = prob.shape[:2]
    xmin = int(np.clip(np.floor(box_points[:, 0].min()), 0, w - 1))
    xmax = int(np.clip(np.ceil(box_points[:, 0].max()), 0, w - 1))
    ymin = int(np.clip(np.floor(box_points[:, 1].min()), 0, h - 1))
    ymax = int(np.clip(np.ceil(box_points[:, 1].max()), 0, h - 1))
    mask = np.zeros((ymax - ymin + 1, xmax - xmin + 1), np.uint8)
    cv2.fillPoly(mask, [(box_points - np.array([xmin, ymin])).astype(np.int32)], 1)
    return float(cv2.mean(prob[ymin:ymax + 1, xmin:xmax + 1], mask=mask)[0])


def _unclip(box, ratio=1.5):
    """DB unclip: area*ratio/perimeter offset, exactly as PaddleOCR does.

    Uses pyclipper when installed (it changes the crop height enough to matter:
    a centroid-scaled approximation gave 'NAN-ING' where the real offset gives
    'NANJING'), and falls back to scaling about the centroid otherwise.
    """
    poly = np.asarray(box, np.float32).reshape(-1, 2)
    area = abs(cv2.contourArea(poly))
    if area < 1e-6:
        return poly
    try:
        import pyclipper
        pts = [(float(p[0]), float(p[1])) for p in poly]
        length = float(np.linalg.norm(np.roll(poly, -1, axis=0) - poly, axis=1).sum())
        if length < 1e-6:
            return poly
        off = pyclipper.PyclipperOffset()
        off.AddPath(pts, pyclipper.JT_ROUND, pyclipper.ET_CLOSEDPOLYGON)
        return np.array(off.Execute(area * ratio / length)[0], np.float32)
    except Exception:
        c = poly.mean(axis=0)
        return (c + (poly - c) * math.sqrt(1.0 + ratio * 0.5)).astype(np.float32)


def detect(img, limit=736):
    """Text line quads in original-image pixel coordinates."""
    det, _ = load()
    padded, ratio = _resize_to_limit(np.asarray(img, np.float32), limit)
    inp = (((padded / 255.0) - DET_MEAN) / DET_STD).transpose(2, 0, 1)[None].astype(np.float32)
    prob = det.run(None, {"x": inp})[0][0, 0]
    mask = (prob > 0.3).astype(np.uint8)
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for c in cnts:
        if len(c) < 4:
            continue
        box, sside = _mini_boxes(c)
        if sside < 3 or _box_score(prob, box) < 0.5:
            continue
        box = _unclip(box)
        box, sside = _mini_boxes(box.reshape(-1, 1, 2))
        if sside < 5:
            continue
        boxes.append(box / ratio)
    return boxes


# ------------------------------------------------------------- CTC recognition

def _crop(img, points):
    points = np.asarray(points, np.float32).reshape(-1, 2)
    w = int(max(np.linalg.norm(points[0] - points[1]), np.linalg.norm(points[2] - points[3])))
    h = int(max(np.linalg.norm(points[0] - points[3]), np.linalg.norm(points[1] - points[2])))
    w, h = max(w, 1), max(h, 1)
    dst = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    M = cv2.getPerspectiveTransform(points, dst)
    crop = cv2.warpPerspective(img, M, (w, h), borderMode=cv2.BORDER_REPLICATE,
                               flags=cv2.INTER_CUBIC)
    if crop.shape[0] / crop.shape[1] >= 1.5:
        crop = np.rot90(crop)
    return crop


def _ctc(probs):
    chars = _chars()
    idx, conf = probs.argmax(1), probs.max(1)
    out, scores, prev = "", [], -1
    for i, c in zip(idx, conf):
        if i != 0 and i != prev:
            out += chars[i]
            scores.append(float(c))
        prev = i
    return out, (float(np.mean(scores)) if scores else 0.0)


def _rec_once(rec, crop, img_h=48, width=None) -> tuple[str, float]:
    """One crop -> (text, conf). Width is padded on the right, as PaddleOCR does."""
    h, w = crop.shape[:2]
    ratio = w / float(h) if h else 1.0
    imgW = width or max(32, min(320, int(math.ceil(img_h * ratio / 8.0)) * 8))
    rw = min(imgW, max(1, int(math.ceil(img_h * ratio))))
    resized = cv2.resize(crop, (rw, img_h)).astype(np.float32)
    resized = ((resized / 255.0) - 0.5) / 0.5
    padded = np.zeros((img_h, imgW, 3), np.float32)
    padded[:, :rw, :] = resized
    inp = np.ascontiguousarray(padded.transpose(2, 0, 1)[None], dtype=np.float32)
    return _ctc(rec.run(None, {"x": inp})[0][0])


def recognize(img, boxes, img_h=48):
    _, rec = load()          # load() returns (det, rec) -- order matters
    out = []
    for box, crop in zip(boxes, [_crop(img, b) for b in boxes]):
        if crop.shape[0] < 4 or crop.shape[1] < 6:
            continue                      # detector noise, not a text line
        try:
            text, conf = _rec_once(rec, crop, img_h)
        except Exception:
            # very wide lines make the model's internal shapes disagree; the
            # standard 320-wide canvas always works, so retry once at that size
            try:
                text, conf = _rec_once(rec, crop, img_h, width=320)
            except Exception:
                continue
        out.append({"text": text, "conf": conf, "box": _round(box)})
    return out


def _round(box):
    pts = np.asarray(box, np.float32).reshape(-1, 2)
    return [float(pts[:, 0].min()), float(pts[:, 1].min()),
            float(pts[:, 0].max()), float(pts[:, 1].max())]


def run(img, min_conf=0.5):
    """OCR an image: [{text, conf, box:[x1,y1,x2,y2]}] sorted top-to-bottom."""
    boxes = detect(img)
    if not boxes:
        return []
    res = [r for r in recognize(img, boxes) if r["text"].strip() and r["conf"] >= min_conf]
    res.sort(key=lambda r: (round(r["box"][1] / 10), r["box"][0]))
    return res
