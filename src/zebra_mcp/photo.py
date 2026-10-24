"""Photo of an existing label -> label spec -> ZPL, inside this project.

The chain: find and rectify the label, decode every symbol (zxing-cpp), read the
text (PP-OCRv4 via .ocr, optional), drop OCR boxes that are really barcodes, map
pixels to dots, emit a spec that goes through the same builder/validator as
everything else.

Geometry rules baked in (each one cost real paper to learn):
  * the label, not the photo, is the reference frame -- an uncropped photo makes
    every element 40-60% too small;
  * mapping is uniform + centred, never per-axis stretch;
  * a 1D module narrower than 2 dots at 203dpi is refused with a warning: the
    printer renders it so thin that scanners report Code 128 as Code 39;
  * human-readable lines under a symbol are merged into the symbol (^BC ...,Y)
    instead of being printed twice;
  * photos cannot invent fonts or logos -- CJK text needs a resident TrueType,
    and bitmaps are out of scope (no ~DG/^GF here).
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import cv2
import numpy as np

from . import builder

__all__ = ["read_image", "find_label_quad", "warp_label", "decode_symbols",
           "measure_bar_rows", "convert"]

# zxing format name -> builder symbology key
_FORMATS = {
    "Code128": "code128", "Code39": "code39", "Code93": "code93",
    "Codabar": "codabar", "ITF": "i2of5", "Interleaved2of5": "i2of5",
    "EAN13": "ean13", "EAN8": "ean8", "UPCA": "upca", "UPCE": "upce",
    "QRCode": "qrcode", "MicroQRCode": "qrcode", "DataMatrix": "datamatrix",
    "PDF417": "pdf417", "MicroPDF417": "micropdf417", "Aztec": "aztec",
    "MaxiCode": "upsmaxicode", "DataBar": "gs1databar",
    "DataBarExpanded": "gs1databar", "MSI": "msi", "Plessey": "plessey",
}
_TWO_D = {"QRCode", "MicroQRCode", "DataMatrix", "PDF417", "MicroPDF417", "Aztec", "MaxiCode"}

# measured on rendered ZPL: printed cap height ~0.775 x font size, ASCII advance
# ~0.51 x size, CJK advance ~1.0 x size
_CAP_RATIO = 0.775
_ASCII_ADV = 0.51
_WIDTH_SLACK = 0.92        # ink width / advance width, measured
BAR_ROW_RATIO = 0.30          # ink ratio that separates bars from HRI text
MIN_MODULE_DOTS = 2           # 203dpi floor; below this the symbol will not scan


def read_image(path: str | Path) -> np.ndarray:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(path)
    buf = np.fromfile(str(p), dtype=np.uint8)      # unicode-safe on Windows
    img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"cannot decode image: {p}")
    return img


def _quad_from_contour(c):
    peri = cv2.arcLength(c, True)
    for eps in (0.02, 0.03, 0.05, 0.08):
        ap = cv2.approxPolyDP(c, eps * peri, True)
        if len(ap) == 4 and cv2.contourArea(ap) > 0.05 * cv2.contourArea(c):
            return ap.reshape(4, 2).astype(np.float32)
    return None


def _order_quad(q):
    s = q.sum(axis=1)
    d = np.diff(q, axis=1).ravel()
    return np.array([q[np.argmin(s)], q[np.argmin(d)], q[np.argmax(s)], q[np.argmax(d)]],
                    np.float32)


def find_label_quad(img) -> np.ndarray | None:
    """Largest plausible label quad, or None. Rejects the frame-sized background."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    h, w = gray.shape[:2]
    area_img = float(h * w)
    best, best_area = None, 0.0
    for flag in (cv2.THRESH_BINARY, cv2.THRESH_BINARY_INV):
        _, th = cv2.threshold(gray, 0, 255, flag + cv2.THRESH_OTSU)
        th = cv2.morphologyEx(th, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8), iterations=2)
        cnts, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in cnts:
            area = cv2.contourArea(c)
            if not (0.10 * area_img < area < 0.92 * area_img):
                continue        # too small, or the background itself
            q = _quad_from_contour(c)
            if q is None:
                continue
            if area > best_area:
                best, best_area = q, area
    return None if best is None else _order_quad(best)


def warp_label(img, quad, long_edge: int = 1600, aspect: float | None = None):
    """Rectify the paper quad to a rectangle, optionally of a *known* aspect.

    Passing aspect is not cosmetic. A square label photographed at an angle is a
    trapezoid: its outline measures 1.12 x 1.00 in the photo. Warping that outline
    to its own bounding aspect bakes the 12% error in, and the content then scales
    down by the same 12% in the square label frame. With the real aspect (label
    w/h) the four corners are mapped onto a true rectangle, which is exactly the
    perspective rectification the photos need.
    """
    tl, tr, br, bl = quad
    w = int(max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl)))
    h = int(max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr)))
    if w <= 0 or h <= 0:
        return img
    if aspect and aspect > 0:
        if w >= h:
            h = max(1, int(round(w / aspect)))
        else:
            w = max(1, int(round(h * aspect)))
    scale = long_edge / float(max(w, h))
    size = (max(int(w * scale), 1), max(int(h * scale), 1))
    dst = np.float32([[0, 0], [size[0] - 1, 0], [size[0] - 1, size[1] - 1], [0, size[1] - 1]])
    M = cv2.getPerspectiveTransform(quad, dst)
    return cv2.warpPerspective(img, M, size, flags=cv2.INTER_CUBIC)


def decode_symbols(img) -> list[dict]:
    import zxingcpp
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    out = []
    for r in zxingcpp.read_barcodes(gray, try_rotate=True, try_downscale=True,
                                    try_invert=True):
        pts = getattr(r, "position", None)
        if pts is not None:
            xs = [p.x for p in [pts.top_left, pts.top_right, pts.bottom_right, pts.bottom_left]]
            ys = [p.y for p in [pts.top_left, pts.top_right, pts.bottom_right, pts.bottom_left]]
            box = [float(min(xs)), float(min(ys)), float(max(xs)), float(max(ys))]
        else:
            box = [0.0, 0.0, float(img.shape[1]), float(img.shape[0])]
        name = r.format.name
        out.append({"format": name, "text": r.text, "box": box, "is_2d": name in _TWO_D,
                    "symbology": _FORMATS.get(name)})
    out.sort(key=lambda e: (e["box"][1], e["box"][0]))
    return out


def measure_bar_rows(gray, x1: int, x2: int, y_hint: float, ratio=BAR_ROW_RATIO) -> tuple[int, int]:
    """Bar band (top, bottom) in full pixels, by ink coverage between x1..x2."""
    x1, x2 = max(0, int(x1)), min(gray.shape[1], int(x2))
    if x2 - x1 < 8:
        return 0, 0
    ink = (gray[:, x1:x2] < 128).mean(axis=1)
    rows = np.where(ink >= ratio)[0]
    if rows.size == 0:
        return 0, 0
    hint = int(np.clip(y_hint, 0, gray.shape[0] - 1))
    bands, start = [], rows[0]
    for prev, cur in zip(rows, rows[1:]):
        if cur != prev + 1:
            bands.append((start, prev))
            start = cur
    bands.append((start, rows[-1]))
    bands = [b for b in bands if b[1] - b[0] >= 5]
    if not bands:
        return 0, 0
    inside = [b for b in bands if b[0] - 10 <= hint <= b[1] + 10]
    return inside[0] if inside else bands[0]


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    return re.sub(r"[^0-9A-Za-z]", "", s).upper()


def _inside_frac(box, ref) -> float:
    x1, y1, x2, y2 = box
    rx1, ry1, rx2, ry2 = ref
    ix = max(0.0, min(x2, rx2) - max(x1, rx1))
    iy = max(0.0, min(y2, ry2) - max(y1, ry1))
    area = max(1.0, (x2 - x1) * (y2 - y1))
    return ix * iy / area


# Total module counts for the fixed-width retail symbols. The generic estimate
# below gave EAN-13 178 modules instead of 95, which halved the module width and
# silently printed the barcode 10 mm narrower than the original.
_FIXED_MODULES = {"ean13": 95, "upca": 95, "ean8": 67, "upce": 51}


def _modules_1d(text: str, symbology: str | None) -> int:
    n = len(text)
    if symbology in _FIXED_MODULES:
        return _FIXED_MODULES[symbology]
    if symbology in ("code128", "gs1_128"):
        digits = text.isdigit() and n >= 4
        symbols = (n + 1) // 2 if digits else n      # set C packs two digits
        return 11 * symbols + 35
    if symbology == "code39":
        return 16 * n + 25
    return 11 * n + 35                               # estimate for the rest


def _qr_modules(text: str) -> int:
    # rough version estimate; only used to pick a magnification
    n = len(text)
    for version, cap in ((1, 14), (2, 26), (3, 42), (4, 62), (5, 84), (6, 106), (7, 122)):
        if n <= cap:
            return 21 + 4 * (version - 1)
    return 45


def _advance(text: str) -> float:
    """Width in units of font size (CJK 1.0, ASCII 0.51)."""
    return sum(1.0 if ord(ch) > 0x2E80 else _ASCII_ADV for ch in text) or 1.0


def convert(image: str | Path, *, width_mm: float = 60.0, height_mm: float = 40.0,
            dpi: int = 203, frame: str = "auto", use_ocr: bool = True,
            font_file: str = "E:SIMSUN.TTF", min_conf: float = 0.5,
            llm_url: str = "", llm_key: str = "", llm_model: str = "",
            vision_timeout: float = 60.0,
            paper_mm: float = 0.0, paper_height_mm: float = 0.0,
            offset_x_mm: float = 0.0, offset_y_mm: float = 0.0) -> dict:
    """Photo -> {zpl, spec, elements, warnings} (no printing, no side effects).

    Text comes from PP-OCRv4 when the local weights are present, otherwise from a
    vision LLM when llm_url/llm_key/llm_model (or ZEBRA_VISION_*) is configured.
    Barcode content always comes from zxing: a VLM's digits are not verifiable.

    paper_mm / paper_height_mm: the size of the physical paper you photographed,
    when it is bigger than the printed area (common on die-cut stock: a 77 mm label
    carrying a 70 mm design). The detected edge is the paper, so without this the
    whole design is scaled down by the paper/design ratio -- measured at 8-10% on a
    77 mm label, which is exactly the "everything is slightly smaller" complaint.
    Leave 0 to assume the paper edge is the print area.
    """
    if not Path(image).exists():
        return {"ok": False, "error": f"image not found: {image}"}
    img = read_image(image)
    warnings: list[dict] = []
    basis = "full"
    paper_w = paper_mm if paper_mm and paper_mm > 0 else width_mm
    paper_h = paper_height_mm if paper_height_mm and paper_height_mm > 0 else height_mm
    if frame in ("auto", "label"):
        quad = find_label_quad(img)
        if quad is not None:
            img, basis = warp_label(img, quad, aspect=paper_w / paper_h), "label"
        elif frame == "label":
            warnings.append({"code": "NO_QUAD",
                             "message": "no label quad found; falling back to the whole photo"})

    h_px, w_px = img.shape[:2]
    pw, ll = builder.mm2dots(width_mm, dpi), builder.mm2dots(height_mm, dpi)
    src_ratio, dst_ratio = w_px / float(h_px), pw / float(ll)
    if abs(src_ratio / dst_ratio - 1) > 0.12:
        warnings.append({"code": "ASPECT",
                         "message": f"photo aspect {src_ratio:.2f} vs label {dst_ratio:.2f} — "
                                    f"check width_mm/height_mm, a wrong template scales every "
                                    f"element and the error grows towards the bottom"})

    # The warped frame is the PAPER; the design sits inside it. Map px through the
    # paper size, then centre the print area within the paper, so a 70 mm design on
    # a 77 mm label keeps its real size instead of shrinking by 70/77.
    pw_paper, ll_paper = builder.mm2dots(paper_w, dpi), builder.mm2dots(paper_h, dpi)
    scale, scale_y = pw_paper / float(w_px), ll_paper / float(h_px)
    if abs(scale / scale_y - 1) > 0.02:
        scale = min(scale, scale_y)                 # never stretch one axis alone
    off_x, off_y = (pw - w_px * scale) / 2.0, (ll - h_px * scale) / 2.0

    nudge_x = builder.mm2dots(offset_x_mm, dpi) if offset_x_mm else 0.0
    nudge_y = builder.mm2dots(offset_y_mm, dpi) if offset_y_mm else 0.0

    def to_dots(box):
        x1, y1, x2, y2 = box
        return [off_x + x1 * scale + nudge_x, off_y + y1 * scale + nudge_y,
                off_x + x2 * scale + nudge_x, off_y + y2 * scale + nudge_y]

    symbols = decode_symbols(img)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img

    texts: list[dict] = []
    source = "local_ocr"
    from . import vision
    want_vision = bool(llm_url or llm_key or llm_model or vision.available().get("ok"))
    if use_ocr:
        from . import ocr
        state = ocr.available()
        if state.get("ok"):
            texts = ocr.run(img, min_conf=min_conf)
        else:
            source = "none"
            warnings.append({"code": "NO_OCR", "message": state.get("error", "OCR unavailable")})
    if want_vision and (not texts or llm_url or llm_model):
        # explicit arguments mean "use the model", not "if local OCR failed"
        vr = vision.analyze(img, base_url=llm_url, api_key=llm_key, model=llm_model,
                            timeout=vision_timeout)
        if vr.get("ok"):
            source = "vision"
            hints = []
            for el in vr["elements"]:
                x, y, w, h = el["box"]
                box = [x * w_px, y * h_px, (x + w) * w_px, (y + h) * h_px]
                if el["kind"] == "barcode":
                    hints.append({"text": el["text"], "format": el.get("format"), "box": box})
                else:
                    texts.append({"text": el["text"], "conf": 1.0, "box": box})
            warnings.append({"code": "VISION_USED",
                             "message": f"text read by {vr.get('model')} "
                                        f"(tokens {vr.get('usage', {}).get('total_tokens', '?')})"})
            if hints:
                warnings.append({"code": "VISION_BARCODE_HINT",
                                 "message": f"model reported {len(hints)} barcode value(s): "
                                            + ", ".join(repr(h["text"]) for h in hints)})
            if not symbols and hints:
                # nothing decoded: offer the model's values, clearly unverified
                for hint in hints:
                    symbols.append({"format": hint["format"] or "Code128",
                                    "text": hint["text"], "box": hint["box"],
                                    "is_2d": _is_2d_name(hint["format"]),
                                    "hri": True, "unverified": True})
                warnings.append({"code": "BARCODE_UNVERIFIED",
                                 "message": "no symbol decoded; barcode content came from the model "
                                            "and was NOT verified — print one and scan it"})
        else:
            warnings.append({"code": "VISION_FAILED", "message": vr.get("error", "vision call failed")})
            if not texts:
                source = "none"

    # Human-readable line under a 1D symbol: OCR usually gets one character wrong
    # ('ABC123' -> 'ABC1Z3'), so match on position, not equality, or the label
    # ends up with the line printed twice (once by ^BC ...,Y, once as text).
    import difflib
    still = []
    for t in texts:
        merged = False
        for s in symbols:
            if s["is_2d"]:
                continue
            bx1, by1, bx2, by2 = s["box"]
            tx1, ty1, tx2, ty2 = t["box"]
            bar_h = max(1.0, by2 - by1)
            gap = ty1 - by2
            # widen the symbol's span before testing overlap: EAN-13 prints its first
            # digit *left of* the bars, so a strict overlap test leaves that digit
            # behind and it gets printed twice (once by the HRI line, once as text)
            slack = 0.2 * (bx2 - bx1)
            overlap = max(0.0, min(tx2, bx2 + slack) - max(tx1, bx1 - slack)) / max(1.0, tx2 - tx1)
            if -0.25 * bar_h <= gap <= 1.2 * bar_h and overlap >= 0.30:
                sim = difflib.SequenceMatcher(None, _norm(t["text"]), _norm(s["text"])).ratio()
                # similarity is only a tie-breaker: a lone misread digit scores ~0 and
                # would otherwise be printed *and* drawn by the symbol's own HRI line
                if sim >= 0.5 or len(_norm(t["text"])) <= 2:
                    s["hri"] = True
                    merged = True
                    warnings.append({"code": "HRI_MERGED",
                                     "message": f"{t['text']!r} under the {s['format']} matched "
                                                f"{s['text']!r} ({sim:.0%}) -> printed by the symbol"})
                    break
        if not merged:
            still.append(t)
    texts = still

    # OCR sees the barcode itself as a text line: drop what lives inside a symbol
    kept = []
    for t in texts:
        hit = next((s for s in symbols if _inside_frac(t["box"], s["box"]) >= 0.5), None)
        if hit is not None:
            if _norm(t["text"]) == _norm(hit["text"]):
                hit["hri"] = True                     # printed by ^BC ...,Y
            warnings.append({"code": "OCR_DROPPED",
                             "message": f"{t['text']!r} sits on the {hit['format']} and was merged"})
            continue
        kept.append(t)
    texts = kept

    # A 4-pixel smudge is not a text field: ^A's minimum scalable height is 10 dots
    # (manual p.60), so anything shorter cannot be printed at all — it is speckle.
    kept = []
    for t in texts:
        _d = to_dots(t["box"])
        height_dots = _d[3] - _d[1]
        if height_dots < 10:
            warnings.append({"code": "OCR_DROPPED",
                             "message": f"{t['text']!r} is {height_dots:.0f} dots tall, below the "
                                        f"^A minimum of 10 — treated as speckle, not text"})
            continue
        kept.append(t)
    texts = kept

    # A torn paper edge or an off-centre design can map content past the top-left
    # corner; one global translate brings it back without distorting anything.
    _boxes = [to_dots(s["box"]) for s in symbols] + [to_dots(t["box"]) for t in texts]
    if _boxes:
        _dx = max(0.0, 2.0 - min(b[0] for b in _boxes))
        _dy = max(0.0, 2.0 - min(b[1] for b in _boxes))
        if _dx or _dy:
            _base = to_dots

            def to_dots(box, _b=_base, _x=_dx, _y=_dy):
                x1, y1, x2, y2 = _b(box)
                return [x1 + _x, y1 + _y, x2 + _x, y2 + _y]

            warnings.append({"code": "SHIFTED",
                             "message": f"content mapped {_dx:.0f}/{_dy:.0f} dots outside the "
                                        f"label; everything was shifted back on-canvas "
                                        f"(paper margin or a torn edge)"})

    fields: list[dict] = []
    elements: list[dict] = []

    for sym in symbols:
        d = to_dots(sym["box"])
        bw, bh = d[2] - d[0], d[3] - d[1]
        if sym["is_2d"]:
            modules = _qr_modules(sym["text"])
            mag = max(1, int(round(bw / modules)))
            fields.append({"type": "barcode", "symbology": sym["symbology"] or "qrcode",
                           "data": sym["text"], "x_mm": _mm(d[0], dpi), "y_mm": _mm(d[1], dpi),
                           "scale": min(mag, 10)})
            elements.append({"kind": "barcode2d", "format": sym["format"], "text": sym["text"],
                             "dots": [round(v, 1) for v in d], "magnification": min(mag, 10)})
            continue
        top, bottom = measure_bar_rows(gray, sym["box"][0], sym["box"][2],
                                       (sym["box"][1] + sym["box"][3]) / 2.0)
        if bottom > top:
            d[1], d[3] = off_y + top * scale, off_y + bottom * scale
            bh = d[3] - d[1]
        modules = _modules_1d(sym["text"], sym["symbology"])
        module = bw / float(max(modules, 1))
        module_i = int(round(module))
        if module_i < MIN_MODULE_DOTS:
            warnings.append({"code": "MODULE_TOO_SMALL",
                             "message": f"{sym['format']} needs {module:.2f} dots/module; "
                                        f"raised to {MIN_MODULE_DOTS} — it will be wider than the "
                                        f"original, otherwise scanners misread the symbology"})
            module_i = MIN_MODULE_DOTS
        module_i = min(module_i, 10)
        fields.append({"type": "barcode", "symbology": sym["symbology"] or "code128",
                       "data": sym["text"], "x_mm": _mm(d[0], dpi), "y_mm": _mm(d[1], dpi),
                       "height_mm": _mm(bh, dpi), "module_width": module_i,
                       "interpret": bool(sym.get("hri"))})
        elements.append({"kind": "barcode1d", "format": sym["format"], "text": sym["text"],
                         "dots": [round(v, 1) for v in d], "module_width": module_i,
                         "bar_height": round(bh, 1), "hri": bool(sym.get("hri"))})

    for t in texts:
        d = to_dots(t["box"])
        bw, bh = d[2] - d[0], d[3] - d[1]
        size = bh / _CAP_RATIO                    # from cap height
        # ink width is ~0.92 of the advance width, so clamping by the raw ink box
        # shrinks every text run by 8% — measured on a label we had printed ourselves
        size = min(size, bw / (_advance(t["text"]) * _WIDTH_SLACK))
        size = max(8.0, min(size, 320.0))
        field = {"type": "text", "data": t["text"], "x_mm": _mm(d[0], dpi),
                 "y_mm": _mm(d[1], dpi), "size_dots": int(round(size))}
        if any(ord(ch) > 0x2E80 for ch in t["text"]):
            field["font_file"] = font_file
        fields.append(field)
        elements.append({"kind": "text", "text": t["text"], "conf": round(t["conf"], 3),
                         "dots": [round(v, 1) for v in d], "font_dots": int(round(size))})

    spec = {"dpi": dpi, "width_mm": width_mm, "height_mm": height_mm, "fields": fields}
    zpl, issues = builder.build_label(spec)
    return {"ok": True, "zpl": zpl, "spec": spec, "elements": elements,
            "warnings": warnings, "issues": issues, "basis": basis,
            "photo_px": [w_px, h_px], "label_dots": [pw, ll], "scale": round(scale, 4),
            "source": source,
            "decoded": [{"format": s["format"], "text": s["text"],
                         **({"unverified": True} if s.get("unverified") else {})} for s in symbols],
            "symbol_count": len(symbols), "text_count": len(texts)}


def _is_2d_name(fmt: str | None) -> bool:
    """Same set as _TWO_D, tolerant of case and of 'QR-Code'-style spellings."""
    if not fmt:
        return False
    key = str(fmt).replace("-", "").replace(" ", "").lower()
    return any(key == n.replace("-", "").lower() for n in _TWO_D)


def _mm(dots: float, dpi: int) -> float:
    return round(float(dots) * 25.4 / dpi, 2)
