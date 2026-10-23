"""Offline ZPL -> PNG preview so an agent can *look* at what it just built.

This is a layout renderer, not a ZPL emulator. It understands the subset this
project's builder emits (^FO/^FT, ^A/^A@ text, ^FB blocks, the mainstream 1D
symbols, QR, ^GB/^GC, ^FR), draws it at 1 dot = 1 px, and reports everything it
could not draw faithfully in `approximated`. That is enough to catch the failures
that matter before paper is spent: overlaps, overflow off the edge, text wider
than its block, a barcode wider than the space left beside it.

Not a substitute for the printer: glyph shapes, module rounding and the printer's
own font metrics differ. Verticals (^FR) are marked approximated.
"""
from __future__ import annotations

import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from . import builder

__all__ = ["render"]

CMD_RE = re.compile(r"([\^~])([A-Z@][A-Z0-9]?)")

# 1D symbologies python-barcode can produce; everything else is drawn as a
# labelled placeholder so the layout is still checkable.
_BARCODE_LIB = {
    "code128": "code128", "code39": "code39", "ean13": "ean13", "ean8": "ean8",
    "upca": "upca", "i2of5": "itf", "codabar": "codabar", "gs1_128": "gs1_128",
}
# BARCODE_CMDS maps symbology -> (command, human name); render needs the reverse
_CMDS = {spec["cmd"].lstrip("^"): key for key, spec in builder.BARCODE_SPECS.items()}

_FONT_CANDIDATES = [
    r"C:/Windows/Fonts/msyh.ttc", r"C:/Windows/Fonts/msyh.ttf",
    r"C:/Windows/Fonts/arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def _font(size: int) -> ImageFont.FreeTypeFont:
    for path in _FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, max(4, int(size)))
            except Exception:
                continue
    return ImageFont.load_default()


# Calibration against Zebra's own rendering of ^A0N (Labelary, 8 dpmm):
# a 43-dot font prints cap height 33 dots and "NANJING" 152 dots wide, while a
# same-height msyh run is ~0.76x too wide. Two constants, both measured.
_INK_RATIO = 1.0       # PIL font size / ZPL height  (a 43-dot ^A0 prints 34-dot caps)
_WIDTH_SCALE = 1.0     # extra horizontal squeeze, measured, not guessed


def _fit_font(height_dots: int) -> ImageFont.FreeTypeFont:
    return _font(max(4, int(round(height_dots * _INK_RATIO))))


def _params(args: str, letters: str) -> dict:
    """Map a ZPL parameter string onto the letters of this command's Format line."""
    out = {}
    if not args:
        return out
    parts = args.split(",")
    for i, letter in enumerate(letters):
        if i < len(parts) and parts[i] != "":
            out[letter] = parts[i]
    return out


def _draw_1d(draw, x, y, height, module_width, pattern, color="black"):
    cx = x
    for run_char, run_len in _runs(pattern):
        w = run_len * module_width
        if run_char == "1":
            draw.rectangle([cx, y, cx + w - 1, y + height - 1], fill=color)
        cx += w
    return cx


def _runs(pattern):
    if not pattern:
        return
    cur, n = pattern[0], 0
    for ch in pattern:
        if ch == cur:
            n += 1
        else:
            yield cur, n
            cur, n = ch, 1
    yield cur, n


def _wrap(text, font, max_w, draw):
    if not max_w:
        return [text]
    lines, cur = [], ""
    for ch in text:
        if draw.textlength(cur + ch, font=font) <= max_w:
            cur += ch
        else:
            lines.append(cur)
            cur = ch
    lines.append(cur)
    return lines


def render(zpl: str, *, dpi: int = 203, scale: int = 1, out: str | Path | None = None) -> dict:
    """ZPL -> {"ok", "png", "dots", "drawn", "approximated", "ignored", "warnings"}."""
    text = zpl.replace("\r", "")
    pw = ll = 0
    for m in re.finditer(r"\^PW(\d+)", text):
        pw = int(m.group(1))
    for m in re.finditer(r"\^LL(\d+)", text):
        ll = int(m.group(1))
    if not pw or not ll:
        # the builder always emits both; anything else gets a 4x6in default
        pw = pw or builder.mm2dots(104, dpi)
        ll = ll or builder.mm2dots(152, dpi)

    img = Image.new("RGB", (max(1, pw), max(1, ll)), "white")
    draw = ImageDraw.Draw(img)
    drawn, approximated, ignored, warnings = [], [], [], []

    x = y = 0
    fh = fw = None
    by_mw, by_ratio, by_h = builder.BY_W, builder.BY_R, builder.BY_H
    fb = {}
    invert = False
    pending = None          # ("text"|barcode spec dict) waiting for ^FD

    tokens = list(CMD_RE.finditer(text))
    for i, tok in enumerate(tokens):
        sig, cmd = tok.group(1), tok.group(2)
        end = tokens[i + 1].start() if i + 1 < len(tokens) else len(text)
        args = text[tok.end():end]
        if sig == "~":
            ignored.append(f"~{cmd}")
            continue

        if cmd == "FO" or cmd == "FT":
            p = args.split(",")
            x = int(p[0] or 0)
            y = int(p[1] or 0) if len(p) > 1 else 0
        elif cmd == "A" or (cmd.startswith("A") and len(cmd) == 2):
            p = args.split(",")
            # ^A0N,h,w  /  ^A@N,h,w,file  — orientation is the letter inside args[0]
            first = p[0] if p else ""
            rest = first[1:] if first[:1].isalpha() else first
            vals = ([rest] if rest else []) + p[1:]
            nums = [int(v) if v.strip().isdigit() else None for v in vals]
            fh = nums[0] if nums else None
            fw = nums[1] if len(nums) > 1 else None
            pending = {"kind": "text"}
        elif cmd == "BY":
            p = (args or "").split(",")
            if p and p[0]:
                by_mw = int(p[0])
            if len(p) > 1 and p[1]:
                by_ratio = float(p[1])
            if len(p) > 2 and p[2]:
                by_h = int(p[2])
        elif cmd == "FB":
            p = args.split(",")
            fb = {"width": int(p[0]) if p and p[0] else None,
                  "lines": int(p[1]) if len(p) > 1 and p[1] else 1,
                  "lh": int(p[2]) if len(p) > 2 and p[2] else None,
                  "align": p[3].upper() if len(p) > 3 and p[3] else "L"}
        elif cmd == "FR":
            invert = True
            approximated.append("^FR (invert) drawn as normal black")
        elif cmd == "FS":
            pending = None
            fb = {}
            invert = False
            fh = fw = None
        elif cmd == "FD":
            data = args.strip()
            if pending and pending.get("kind") == "text":
                _render_text_block(img, x, y, data, fh, fw, fb, drawn, warnings)
            elif pending and pending.get("kind") == "barcode":
                _render_barcode(draw, x, y, data, pending, by_mw, by_h,
                                drawn, approximated)
            pending = None
        elif cmd in _CMDS:
            sym = _CMDS[cmd]
            spec = builder.BARCODE_SPECS.get(sym) if sym else None
            if not spec:
                ignored.append(f"^{cmd}")
                continue
            p = {**spec["d"], **_params(args, spec["letters"])}   # manual defaults first
            h = int(p.get("h") or by_h or 10)
            pending = {"kind": "barcode", "sym": sym, "spec": spec, "params": p,
                       "height": h, "cmd": cmd}
        elif cmd == "GB":
            p = args.split(",")
            w = int(p[0] or 0)
            hh = int(p[1] or 0)
            t = int(p[2] or 1) if len(p) > 2 and p[2] else 1
            color = (p[3] or "B").upper()
            box = [x, y, x + w - 1, y + hh - 1]
            if color == "W":
                draw.rectangle(box, outline="white", width=t)
            elif t >= max(1, min(w, hh)):
                draw.rectangle(box, fill="black")
            else:
                draw.rectangle(box, outline="black", width=max(1, t))
            drawn.append({"type": "box", "dots": box})
        elif cmd == "GC":
            p = args.split(",")
            w = int(p[0] or 0)
            hh = int(p[1] or 0)
            t = int(p[2] or 1) if len(p) > 2 and p[2] else 1
            draw.ellipse([x, y, x + w, y + hh], outline="black", width=max(1, t))
            drawn.append({"type": "circle", "dots": [x, y, x + w, y + hh]})
        elif cmd == "XGR":
            name = (args.split(",")[0] or "?")
            draw.rectangle([x, y, x + 80, y + 80], outline="black")
            draw.text((x + 4, y + 30), f"[{name}]", fill="black", font=_font(10))
            approximated.append(f"^XGR:{name} drawn as a placeholder box")
        elif cmd in ("PW", "LL", "CI", "CF", "MD", "PR", "MN", "MM", "LS", "LT",
                     "PM", "PO", "FW", "XA", "XZ", "FX", "FH", "JZ", "LH", "CW"):
            continue
        else:
            ignored.append(f"^{cmd}")

    if scale > 1:
        img = img.resize((img.width * scale, img.height * scale), Image.NEAREST)

    if out is None:
        out = Path(builder.__file__).resolve().parents[2] / "render_preview.png"
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)

    if fb and pending is None:
        pass
    if not drawn:
        warnings.append("nothing drawable found — is this ZPL inside ^XA…^XZ?")
    return {"ok": True, "png": str(out), "dots": [pw, ll], "scale": scale,
            "drawn": drawn, "approximated": approximated,
            "ignored": sorted(set(ignored)), "warnings": warnings}


def _blit_text(img, xy, text, font):
    """Draw text black-on-transparent, squeezed to Zebra's ^A0 width."""
    asc, desc = font.getmetrics()
    probe = ImageDraw.Draw(img)
    tw = int(probe.textlength(text, font=font)) + 2
    layer = Image.new("L", (max(1, tw), asc + desc + 2), 255)
    ImageDraw.Draw(layer).text((0, 0), text, font=font, fill=0)
    if _WIDTH_SCALE != 1.0:
        layer = layer.resize((max(1, int(layer.width * _WIDTH_SCALE)), layer.height),
                             Image.LANCZOS)
    img.paste((0, 0, 0), xy, ImageOps.invert(layer))


def _ink_width(probe, text, font):
    """Ink extent, not advance width — advance includes side bearings the eye can't see."""
    box = probe.textbbox((0, 0), text, font=font)
    return (box[2] - box[0]) * _WIDTH_SCALE


def _render_text_block(img, x, y, data, fh, fw, fb, drawn, warnings):
    h = fh or 20
    font = _fit_font(h)
    max_w = fb.get("width")
    probe = ImageDraw.Draw(img)
    wrap_at = max_w / _WIDTH_SCALE if max_w else None
    lines = _wrap(data, font, wrap_at, probe)
    lh = fb.get("lh") or int(round(h * 1.2))
    align = fb.get("align", "L")
    for idx, line in enumerate(lines):
        tw = _ink_width(probe, line, font)
        dx = x
        if max_w and align == "C":
            dx = x + (max_w - tw) / 2
        elif max_w and align == "R":
            dx = x + max_w - tw
        _blit_text(img, (int(dx), int(y + idx * lh)), line, font)
    full = _ink_width(probe, data, font)
    if max_w and full > max_w:
        warnings.append(f"text {data!r} is {int(full)} dots wide, wider than its "
                        f"^FB block ({max_w} dots)")
    drawn.append({"type": "text", "text": data, "dots": [x, y], "size": h,
                  "width": int(round(full)), "lines": len(lines)})


def _render_barcode(draw, x, y, data, pend, by_mw, by_h, drawn, approximated):
    sym, params, h = pend["sym"], pend["params"], pend["height"]
    interpret = (params.get("f", "N") or "N").upper() == "Y"

    if sym == "qrcode":
        prefix = re.match(r"^([A-Za-z])([A-Za-z]),(.*)$", data, re.S)
        payload = prefix.group(3) if prefix else data
        ec = (prefix.group(1).upper() if prefix else "Q").replace("H", "H")
        ec = {"L": "l", "M": "m", "Q": "q", "H": "h"}.get(ec, "q")
        mag = int(params.get("c") or 2) or 2
        try:
            import segno
            # micro=False: Zebra's ^BQ has no Micro QR, and segno silently picks
            # Micro for short payloads (17 modules instead of 21)
            qr = segno.make(payload, error=ec, micro=False)
            matrix = list(qr.matrix)
        except Exception as exc:
            approximated.append(f"QR {payload!r} not encoded ({exc})")
            return
        # ^BQ's field origin sits inside a quiet zone that is wider vertically than
        # horizontally; both offsets measured against Zebra's own rendering.
        quiet_x, quiet_y = 1, 3
        mod = max(1, mag)
        side = (len(matrix) + quiet_x) * mod
        top = y
        for r, row in enumerate(matrix):
            for c, v in enumerate(row):
                if v:
                    px = x + (c + quiet_x) * mod
                    py = top + (r + quiet_y) * mod
                    draw.rectangle([px, py, px + mod - 1, py + mod - 1], fill="black")
        drawn.append({"type": "qrcode", "text": payload,
                      "dots": [x, top, x + side, top + side]})
        return

    lib_name = _BARCODE_LIB.get(sym)
    if not lib_name:
        draw.rectangle([x, y, x + 120, y + h], outline="black")
        draw.text((x + 3, y + h // 3), f"{sym}", fill="black", font=_font(9))
        approximated.append(f"{sym} is not drawn (no offline encoder) — box only")
        return
    try:
        from barcode import get_barcode
        pattern = get_barcode(lib_name, data).build()[0]
    except Exception as exc:
        draw.rectangle([x, y, x + 120, y + h], outline="black")
        approximated.append(f"{sym} {data!r} not encodable ({exc})")
        return
    mw = max(1, int(by_mw))
    right = _draw_1d(draw, x, y, h, mw, pattern)
    if interpret:
        fnt = _font(12)
        tw = draw.textlength(data, font=fnt)
        draw.text(((x + right) / 2 - tw / 2, y + h + 3), data, fill="black", font=fnt)
        bottom = y + h + 18
    else:
        bottom = y + h
    drawn.append({"type": sym, "text": data, "dots": [x, y, right, bottom],
                  "modules": len(pattern), "module_width": mw})
