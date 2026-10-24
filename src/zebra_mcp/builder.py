"""ZPL II label builder.

Parameter defaults and value ranges taken from the ZPL II Programming Guide
(zpl-zbi2-pg-en-202609.pdf), command pages cited inline:

  ^BY p.148   ^CF p.154   ^CI p.155-157   ^FO p.201   ^FT p.205   ^FB p.186
  ^FS p.204   ^GB p.210   ^GC p.212       ^GS p.217     ^PW p.329  ^LL p.294
  ^MD p.301   ^MM p.305   ^MN p.307       ^LS p.296     ^LT p.297  ^PM p.319
  ^PO p.322   ^PQ p.324   ^PR p.326       ^FW p.208     ^FH p.193  ^FD p.190
  ^A  p.60    ^GS p.217   barcode pages 66-150
"""

from __future__ import annotations

import re

# ---- manual defaults (Initial Value at Power Up) --------------------------------
BY_W, BY_R, BY_H = 2, 3.0, 10          # ^BY p.148: w 1-10, r 2.0-3.0, h 10
CF_FONT, CF_H, CF_W = "0", 9, 5        # ^CF p.154: font A->scalable 0 used, h 9, w 5
CI_DEFAULT = 0                         # ^CI p.155: U.S.A. 1
CI_UNICODE = 28                        # ^CI28-30 recommended for CJK/Unicode (p.157)
FD_MAX = 3072                          # ^FD p.190: string up to 3072 bytes
BARCODE_H_MAX = 32000                  # ^B2 etc.: height 1-32000 dots
FO_MAX = 32000                         # ^FO/^FT: 0-32000 dots
PW_MAX, LL_MAX = 32000, 32000          # ^PW p.329 / ^LL p.294
MD_MIN, MD_MAX = -30, 30               # ^MD p.301
ORIENTATIONS = ("N", "R", "I", "B")
PR_SPEED = {"1": "25.4 mm/s", "2": "50.8 mm/s", "3": "76.2 mm/s", "4": "101.6 mm/s",
            "5": "127 mm/s", "6": "152.4 mm/s", "A": "fastest", "D": "default"}
MM_MODES = ("T", "P", "R", "A", "K")   # ^MM p.305
MM_MEDIA_LOSS = ("N", "Y")             # ^MM p.305 b = media loss flag
MN_MODES = ("N", "Y", "W", "M", "B", "A")  # ^MN p.307
CJK_FONTS = {"0", "15", "16"}          # ^A p.60 font 15/16 = SC/TC Chinese (model limited)

# symbology -> (zpl command, human name) — manual pages 66-150
# Symbologies whose manual Format line defines f/g as interpretation lines
# (below / above the symbol). Everything else uses those letters for other
# parameters, so they must not be filled with Y/N.
INTERP_FG = {"code11", "code128", "code39", "code93", "i2of5", "planet", "postnet",
             "usps", "codabar", "msi", "plessey", "logmars", "std2of5", "ean13",
             "ean8", "upca", "upce", "upcext", "industrial2of5"}

BARCODE_CMDS = {
    "code128": ("^BC", "Code 128"), "code39": ("^B3", "Code 39"),
    "code93": ("^BA", "Code 93"), "i2of5": ("^B2", "Interleaved 2 of 5"),
    "codablock": ("^BB", "CODABLOCK"), "planet": ("^B5", "Planet Code"),
    "postnet": ("^BZ", "POSTNET"), "usps": ("^BZ", "USPS IM Barcode"),
    "upsmaxicode": ("^BD", "UPS MaxiCode"), "pdf417": ("^B7", "PDF417"),
    "micropdf417": ("^BF", "MicroPDF417"), "codabar": ("^BK", "ANSI Codabar"),
    "msi": ("^BM", "MSI"), "plessey": ("^BP", "Plessey"), "logmars": ("^BL", "LOGMARS"),
    "industrial2of5": ("^BI", "Industrial 2 of 5"),
    "std2of5": ("^BJ", "Standard 2 of 5"), "ean13": ("^BE", "EAN-13"),
    "ean8": ("^B8", "EAN-8"), "upca": ("^B9", "UPC-A"), "upce": ("^B0", "UPC-E"),
    "upcext": ("^BS", "UPC/EAN add-on"), "gs1databar": ("^BR", "GS1 Databar"),
    "code49": ("^B4", "Code 49"),
    "datamatrix": ("^BX", "Data Matrix"), "qrcode": ("^BQ", "QR Code"),
    "aztec": ("^BO", "Aztec"),
    "code11": ("^B1", "Code 11"),
}

# symbology -> manual Format line, parameter letters (in order) and defaults.
# `letters` follows the manual's Format line exactly (grep "Format: ^B" in
# data/zpl_manual_full.txt); `orient` names the letter that carries orientation,
# None for symbologies without one (^BD MaxiCode). Defaults are the manual's
# stated defaults; None -> omit the parameter and let the printer/^BY decide.
BARCODE_SPECS = {
    "code11": dict(cmd="^B1", name="Code 11", page="p.72", letters="oehfg",
                   orient="o", d=dict(o="N", e="N", h=None, f="Y", g="N")),
    "code128": dict(cmd="^BC", name="Code 128", page="p.94", letters="ohfgem",
                    orient="o", d=dict(o="N", h=None, f="Y", g="N", e="N", m="N"),
                    extra={"m": ("N", "U", "A")}),
    "code39": dict(cmd="^B3", name="Code 39", page="p.70", letters="oehfg",
                   orient="o", d=dict(o="N", e="N", h=None, f="Y", g="N")),
    "code93": dict(cmd="^BA", name="Code 93", page="p.87", letters="ohfge",
                   orient="o", d=dict(o="N", h=None, f="Y", g="N", e="N")),
    "i2of5": dict(cmd="^B2", name="Interleaved 2 of 5", page="p.68", letters="ohfgej",
                  orient="o", d=dict(o="N", h=None, f="Y", g="N", e="N", j="N")),
    "planet": dict(cmd="^B5", name="Planet Code", page="p.78", letters="ohfg",
                   orient="o", d=dict(o="N", h=None, f="N", g="N")),
    "postnet": dict(cmd="^BZ", name="POSTNET", page="p.150", letters="ohfgt",
                    orient="o", d=dict(o="N", h=None, f="N", g="N", t="0")),
    "usps": dict(cmd="^BZ", name="USPS IM Barcode", page="p.150", letters="ohfgt",
                 orient="o", d=dict(o="N", h=None, f="N", g="N", t="3")),
    "upsmaxicode": dict(cmd="^BD", name="UPS MaxiCode", page="p.106", letters="mnt",
                        orient=None, d=dict(m="2", n="1", t="1")),
    "pdf417": dict(cmd="^B7", name="PDF417", page="p.79", letters="ohscrt",
                   orient="o", d=dict(o="N", h=None, s="0", c="1", r="3", t="N")),
    "micropdf417": dict(cmd="^BF", name="MicroPDF417", page="p.111", letters="ohm",
                        orient="o", d=dict(o="N", h=None, m="0")),
    "codabar": dict(cmd="^BK", name="Codabar", page="p.118", letters="oehfgkl",
                    orient="o", d=dict(o="N", e="N", h=None, f="Y", g="N", k="A", l="A")),
    "msi": dict(cmd="^BM", name="MSI", page="p.122", letters="oehfgE",
                orient="o", d=dict(o="N", e="B", h=None, f="Y", g="N", E="N")),
    "plessey": dict(cmd="^BP", name="Plessey", page="p.126", letters="oehfg",
                    orient="o", d=dict(o="N", e="N", h=None, f="Y", g="N")),
    "logmars": dict(cmd="^BL", name="LOGMARS", page="p.120", letters="ohg",
                    orient="o", d=dict(o="N", h=None, g="N")),
    "industrial2of5": dict(cmd="^BI", name="Industrial 2 of 5", page="p.114", letters="ohfg",
                           orient="o", d=dict(o="N", h=None, f="Y", g="N")),
    "std2of5": dict(cmd="^BJ", name="Standard 2 of 5", page="p.116", letters="ohfg",
                    orient="o", d=dict(o="N", h=None, f="Y", g="N")),
    "ean13": dict(cmd="^BE", name="EAN-13", page="p.109", letters="ohfg",
                  orient="o", d=dict(o="N", h=None, f="Y", g="N")),
    "ean8": dict(cmd="^B8", name="EAN-8", page="p.83", letters="ohfg",
                 orient="o", d=dict(o="N", h=None, f="Y", g="N")),
    "upca": dict(cmd="^B9", name="UPC-A", page="p.85", letters="ohfge",
                 orient="o", d=dict(o="N", h=None, f="Y", g="N", e="N")),
    "upce": dict(cmd="^B0", name="UPC-E", page="p.66", letters="oehfg",
                 orient="o", d=dict(o="N", e="N", h=None, f="Y", g="N")),
    "upcext": dict(cmd="^BS", name="UPC/EAN add-on", page="p.137", letters="ohfg",
                   orient="o", d=dict(o="N", h=None, f="Y", g="Y")),
    "gs1databar": dict(cmd="^BR", name="GS1 DataBar", page="p.135", letters="abcdef",
                       orient="a", d=dict(a="R", b="1", c=None, d="1", e="25", f="22")),  # manual: a defaults to R
    "code49": dict(cmd="^B4", name="Code 49", page="p.74", letters="ohfm",
                   orient="o", d=dict(o="N", h=None, f="N", m="2")),
    "codablock": dict(cmd="^BB", name="CODABLOCK", page="p.99", letters="ohscrm",
                      orient="o", d=dict(o="N", h="8", s="Y", c=None, r=None, m="A")),
    "datamatrix": dict(cmd="^BX", name="Data Matrix", page="p.144", letters="ohscr",
                       orient="o", d=dict(o="N", h=None, s="200", c="0", r="1")),
    "qrcode": dict(cmd="^BQ", name="QR Code", page="p.128", letters="abcde",
                   orient="a", d=dict(a="N", b="2", c="0", d="Q", e="7")),
    "aztec": dict(cmd="^BO", name="Aztec", page="p.124", letters="abcdefg",
                  orient="a", d=dict(a="N", b="2", c="N", d="0", e="N")),
}


def _bool(val, default):
    if val is None:
        return default
    if isinstance(val, bool):
        return "Y" if val else "N"
    v = str(val).strip().upper()
    return v if v in ("Y", "N") else ("Y" if v in ("TRUE", "YES", "1") else "N")


def escape_data(data: str, hex_indicator: str | None) -> str:
    """Return (escaped_data, used_hex).

    ^ and ~ terminate fields inside ^FD (^FD p.190). With ^FH (^FH p.193) they
    are hex-escaped (_5F/_7E); otherwise they are stripped (unavoidable).
    """
    used_hex = False
    data = str(data)
    if any(c in data for c in "^~"):
        if hex_indicator is None:
            hex_indicator = "_"
        used_hex = True
        out = []
        for ch in data:
            if ch in ("^", "~") or ch == hex_indicator:
                out.append(f"{hex_indicator}{ord(ch):02X}")
            else:
                out.append(ch)
        data = "".join(out)
    return data, used_hex


def _render_barcode(f: dict, dpi: int, issues: list) -> tuple[str, bool]:
    sym = str(f.get("symbology", "code128")).lower()
    spec = BARCODE_SPECS.get(sym)
    if spec is None:
        issues.append({"severity": "error", "code": "BARCODE_UNKNOWN",
                       "field": sym, "message": f"Unknown symbology '{sym}'. "
                       f"See zpl_knowledge(mode='barcodes') for the supported list."})
        return "", False
    no_hex = bool(f.get("no_hex", False))
    d = spec["d"]
    params: dict = {}
    # Per-field ^BY: on a label built from a photo two symbols can need different
    # module widths, but ^BY is printer state — re-emit it in front of this symbol.
    by_prefix = ""
    if any(k in f for k in ("module_width", "module_ratio", "bar_height")):
        mw = int(f.get("module_width", BY_W))
        if not (1 <= mw <= 10):
            issues.append({"severity": "warning", "code": "BY_WIDTH", "field": sym,
                           "message": f"module width {mw} outside 1-10 (^BY p.148), using {BY_W}"})
            mw = BY_W
        ratio = f.get("module_ratio", BY_R)
        ratio_s = f"{ratio:g}" if isinstance(ratio, float) else str(ratio)
        bh = int(f.get("bar_height", BY_H))
        by_prefix = f"^BY{mw},{ratio_s},{bh}"

    # Orientation: which letter carries it comes from the manual's Format line
    # (^B9/^BR/^BQ/^BO use 'a' or lead with 'o'; ^BD MaxiCode has none at all).
    orient_letter = spec.get("orient")
    if orient_letter:
        o = str(f.get("orientation", "N")).upper()
        if o not in ORIENTATIONS:
            issues.append({"severity": "warning", "code": "ORIENT", "field": sym,
                           "message": f"orientation '{o}' invalid, using {d.get(orient_letter, 'N')} (manual: N/R/I/B)"})
            o = d.get(orient_letter, "N")
        params[orient_letter] = o
    elif f.get("orientation"):
        issues.append({"severity": "warning", "code": "ORIENT_NA", "field": sym,
                       "message": f"{spec['name']}: {spec['cmd']} has no orientation parameter "
                                  f"(manual Format line {spec['page']})"})

    # bar height override (else ^BY h is used, manual default 10 dots)
    if "height_dots" in f or "height_mm" in f:
        h = int(f["height_dots"]) if "height_dots" in f else mm2dots(f["height_mm"], dpi)
        if not (1 <= h <= BARCODE_H_MAX):
            issues.append({"severity": "error", "code": "BC_HEIGHT", "field": sym,
                           "message": f"barcode height {h} outside 1-{BARCODE_H_MAX} dots"})
        params["h"] = str(h)

    # ---- per-symbology parameters (letters as in the manual Format line) ----
    if sym == "datamatrix":
        if f.get("module_size_dots"):
            m = int(f["module_size_dots"])
            if not (1 <= m <= 25):
                issues.append({"severity": "warning", "code": "DM_MODULE", "field": sym,
                               "message": f"module size {m} outside 1-25 dots (^BX p.144)"})
            params["h"] = str(m)
        if f.get("quality") is not None:
            q = str(f["quality"])
            if q not in ("0", "50", "80", "100", "140", "200"):
                issues.append({"severity": "error", "code": "DM_QUALITY", "field": sym,
                               "message": f"quality '{q}' not in 0/50/80/100/140/200 (^BX p.144)"})
            params["s"] = q
        if f.get("columns") is not None:
            params["c"] = str(int(f["columns"]))
        if f.get("rows") is not None:
            params["r"] = str(int(f["rows"]))
    elif sym == "qrcode":
        c = f.get("scale")
        if c is not None:
            c = int(c)
            if not (1 <= c <= 100):
                issues.append({"severity": "error", "code": "QR_SCALE", "field": sym,
                               "message": f"QR magnification {c} outside 1-100 (^BQ p.128)"})
            params["c"] = str(c)
        ec = str(f.get("ec_level", d["d"])).upper()
        if ec not in ("H", "Q", "M", "L"):
            issues.append({"severity": "error", "code": "QR_EC", "field": sym,
                           "message": f"ec_level '{ec}' not in H/Q/M/L (^BQ p.128)"})
            ec = d["d"]
        params["d"] = ec
        if f.get("model") is not None:
            mval = str(int(f["model"]))
            if mval not in ("1", "2"):
                issues.append({"severity": "warning", "code": "QR_MODEL", "field": sym,
                               "message": "QR model must be 1 or 2; using 2"})
                mval = "2"
            params["b"] = mval
        if f.get("mask") is not None:
            mk = int(f["mask"])
            if not (0 <= mk <= 7):
                issues.append({"severity": "warning", "code": "QR_MASK", "field": sym,
                               "message": f"QR mask {mk} outside 0-7 (^BQ p.128); using 7"})
                mk = 7
            params["e"] = str(mk)
    elif sym in ("pdf417", "micropdf417"):
        if sym == "micropdf417":
            if f.get("mode") is not None:
                mv = int(f["mode"])
                if not (0 <= mv <= 33):
                    issues.append({"severity": "error", "code": "MPDF_MODE", "field": sym,
                                   "message": f"MicroPDF417 mode {mv} outside 0-33 (^BF p.111)"})
                params["m"] = str(mv)
        else:
            for key, letter in (("security_level", "s"), ("columns", "c"), ("row_ratio", "r")):
                if f.get(key) is not None:
                    params[letter] = str(int(f[key]))
    elif sym == "gs1databar":
        if f.get("symbology_type") is not None:
            params["b"] = str(int(f["symbology_type"]))
        if f.get("magnification") is not None:
            params["c"] = str(int(f["magnification"]))
        if f.get("separator_height") is not None:
            params["d"] = str(int(f["separator_height"]))
        if "height_dots" in f or "height_mm" in f:
            params["e"] = params.pop("h")          # ^BR e = barcode height, not h
        if f.get("segment_width") is not None:
            params["f"] = str(int(f["segment_width"]))
    elif sym == "code49":
        if f.get("interpret_pos") is not None:
            fv = str(f["interpret_pos"]).upper()
            if fv not in ("N", "A", "B"):
                issues.append({"severity": "warning", "code": "B4_INTERP", "field": sym,
                               "message": "Code 49 interpretation must be N/A/B (^B4 p.74); using N"})
                fv = "N"
            params["f"] = fv
        if f.get("mode") is not None:
            params["m"] = str(int(f["mode"]))
    elif sym == "codablock":
        if f.get("security") is not None:
            params["s"] = _bool(f["security"], d["s"])
        if f.get("columns") is not None:
            params["c"] = str(int(f["columns"]))
        if f.get("rows") is not None:
            params["r"] = str(int(f["rows"]))
        if f.get("mode") is not None:
            mv = str(f["mode"]).upper()
            if mv not in ("A", "E", "F"):
                issues.append({"severity": "warning", "code": "BB_MODE", "field": sym,
                               "message": "CODABLOCK mode must be A/E/F (^BB p.99); using A"})
                mv = "A"
            params["m"] = mv
    elif sym == "codabar":
        if f.get("start_char"):
            params["k"] = str(f["start_char"]).upper()
        if f.get("stop_char"):
            params["l"] = str(f["stop_char"]).upper()
    elif sym == "upsmaxicode":
        params.update({"m": str(f.get("mode", d["m"])), "n": str(f.get("symbol_number", d["n"])),
                       "t": str(f.get("total_symbols", d["t"]))})
    elif sym == "msi":
        e = str(f.get("check_digit", d["e"])).upper()
        if e not in ("A", "B", "C", "D"):
            issues.append({"severity": "warning", "code": "MSI_CHECK", "field": sym,
                           "message": "MSI check mode must be A/B/C/D; using B"})
            e = "B"
        params["e"] = e
        params["E"] = _bool(f.get("insert_check_digit"), d.get("E", "N"))

    # interpretation line flags (f = below, g = above). Only symbologies whose
    # manual Format line really defines f/g as interpretation lines: on ^BO/^BX/^BR
    # those letters mean other things (symbol count, escape char, segment width).
    for letter in ("fg" if sym in INTERP_FG else ""):
        if letter not in spec["letters"]:      # ^BL has only g (interpretation above)
            continue
        key = "interpret" if letter == "f" else "interpret_above"
        if params.get(letter) is None:
            params[letter] = _bool(f.get(key), d.get(letter, "N"))

    # generic check digit (only symbologies where 'e' means check digit)
    if "e" in spec["letters"] and sym not in ("qrcode", "aztec", "gs1databar", "msi"):
        params["e"] = _bool(f.get("check_digit"), d.get("e", "N"))

    # Code 128 mode
    if spec["cmd"] == "^BC" and "m" in spec["letters"]:
        m = str(f.get("mode", d.get("m", "N"))).upper()
        if m not in ("N", "U", "A"):
            issues.append({"severity": "warning", "code": "C128_MODE", "field": sym,
                           "message": "Code128 mode must be N/U/A (auto); using N"})
            m = "N"
        params["m"] = m

    # emit in Format-line letter order with commas; defaults omitted at the tail,
    # skipped middle parameters left as empty segments (valid ZPL: ^BCN,,Y)
    letters = list(spec["letters"])
    for letter in params:
        if letter not in letters:
            letters.append(letter)
    explicit = {l for l in params if l in letters and str(params[l]) != str(d.get(l))}
    last = max((letters.index(l) for l in explicit), default=-1)
    segs = [params.get(l, "") for l in letters[: last + 1]]
    while segs and segs[-1] == "":
        segs.pop()
    cmd = spec["cmd"]
    if segs:
        cmd += ",".join(segs)

    raw_data = str(f.get("data", ""))
    if sym == "qrcode":
        # ^BQ p.129: the ^FD string MUST open with <error correction><input mode>,
        # e.g. ^FDQA,<data>  (Q = high reliability, A = auto data input)
        mode = str(f.get("input_mode", "A")).upper()
        if mode not in ("A", "M"):
            issues.append({"severity": "warning", "code": "QR_INPUT", "field": sym,
                           "message": "QR input mode must be A (automatic) or M (manual); using A"})
            mode = "A"
        raw_data = f"{params.get('d', d['d'])}{mode},{raw_data}"
    data, used_hex = escape_data(raw_data, None if no_hex else "_")
    fh = "^FH" if used_hex else ""
    # honor field coordinates (^FO before the barcode command, p.201); without
    # this, barcode/QR fields fall back to the current print position
    pos = ""
    if any(k in f for k in ("x_mm", "x_dots", "y_mm", "y_dots")):
        x, y = _xy(f, dpi)
        pos = f"^FO{x},{y}"
    return pos + by_prefix + cmd + fh + "^FD" + data + "^FS", used_hex


def _render_text(f: dict, dpi: int, issues: list) -> tuple[str, bool]:
    font_file = f.get("font_file")  # ^A@ (p.62): call a downloadable/resident font by full name
    if font_file:
        name = str(font_file).strip().upper()
        m = re.fullmatch(r"(R|E|B|A):([A-Z0-9_.\-]+\.(TTF|FNT|TTE))", name)
        if not m:
            issues.append({"severity": "error", "code": "FONT_FILE", "field": name,
                           "message": "font_file must be 'E:NAME.TTF' (drive R/E/B/A + name."
                                      "TTF|.FNT|.TTE) (^A@ p.62)"})
            font_file = None
    font = str(f.get("font", CF_FONT))
    if not font_file and not re.fullmatch(r"[0-9A-Z]", font):
        issues.append({"severity": "error", "code": "FONT", "field": font,
                       "message": "font must be 0-9 or A-Z (^A p.60)"})
        font = "0"
    h = f.get("size_mm")
    if h is not None:
        h_dots = mm2dots(h, dpi)
    elif f.get("size_dots") is not None:
        h_dots = int(f["size_dots"])          # pixel/photo-derived sizes come in dots
    else:
        h_dots = None
    if h_dots is not None and not (10 <= h_dots <= 32000):
        issues.append({"severity": "error", "code": "TEXT_SIZE", "field": font,
                       "message": f"height {h_dots} dots outside scalable range 10-32000 (^A p.60)"})
        h_dots = None
    w_dots = None
    if f.get("width_mm"):
        w_dots = mm2dots(f["width_mm"], dpi)
    elif f.get("width_dots"):
        w_dots = int(f["width_dots"])
    o = str(f.get("orientation", "N")).upper()
    if o not in ORIENTATIONS:
        issues.append({"severity": "warning", "code": "ORIENT", "field": font,
                       "message": f"orientation '{o}' invalid, using N"})
        o = "N"
    origin = f.get("origin", "FO")  # ^FO absolute (p.201) vs ^FT relative text position (p.205)
    if origin not in ("FO", "FT"):
        issues.append({"severity": "error", "code": "ORIGIN", "field": font,
                       "message": "origin must be FO (absolute) or FT (after last text field)"})
        origin = "FO"
    if "x_dots" in f:
        x = int(f["x_dots"])
    elif "x_mm" in f:
        x = mm2dots(f["x_mm"], dpi)
    else:
        x = 0
    y = int(f.get("y_dots", 0)) if "y_dots" in f else (mm2dots(f.get("y_mm", 0), dpi) if "y_mm" in f else 0)
    for label, v in (("x", x), ("y", y)):
        if not (0 <= v <= FO_MAX):
            issues.append({"severity": "error", "code": "POS", "field": font,
                           "message": f"{label} {v} outside 0-{FO_MAX} dots (^FO p.201)"})
    no_hex = bool(f.get("no_hex", False))
    data, used_hex = escape_data(f.get("data", ""), None if no_hex else "_")
    cmd = f"^{origin}{x},{y}"
    if font_file:
        # ^A@ o,h,w,d:f.x (p.62): name carries over to later ^A@ without a name.
        # Emit the width explicitly (manual examples are ^A@N,50,50,B:CYRI_UB.FNT);
        # square glyphs are what CJK fonts want, so width defaults to height.
        w_out = w_dots if w_dots else (h_dots if h_dots is not None else "")
        a = f"^A@{o},{h_dots if h_dots is not None else ''},{w_out}"
        a += f",{str(font_file).strip().upper()}"
    else:
        a = f"^A{font}{o}"
        if h_dots is not None:
            a += f",{h_dots}"
            if w_dots:
                a += f",{w_dots}"
    parts = [cmd, a]
    block = f.get("block")
    if block:
        bw_dots = block.get("width_dots") or (mm2dots(block.get("width_mm", 0), dpi) if block.get("width_mm") else 0)
        lines = int(block.get("lines", 1))
        if not (1 <= lines <= 9999):
            issues.append({"severity": "warning", "code": "FB_LINES", "field": font,
                           "message": f"block lines {lines} outside 1-9999 (^FB p.186), using 1"})
            lines = 1
        lh = int(block.get("line_height", 0))
        align = str(block.get("alignment", "L")).upper()
        if align not in ("L", "C", "R", "J"):
            issues.append({"severity": "warning", "code": "FB_ALIGN", "field": font,
                           "message": f"alignment '{align}' not in L/C/R/J (^FB p.186)"})
            align = "L"
        hi = int(block.get("hanging_indent", 0))
        # ^FB sits between ^A and ^FD (manual example p.186)
        parts.append(f"^FB{bw_dots},{lines},{lh},{align},{hi}")
    parts.append("^FH" if used_hex else "")
    parts.append("^FD" + data)
    parts.append("^FS")
    return "".join(parts), used_hex


def _render_box(f: dict, dpi: int) -> str:
    """^GB graphic box (p.210). 'note' and 'box' are the same ZPL command."""
    x, y, w, h = _xywh(f, dpi)
    return (f"^FO{x},{y}^GB{w},{h},{int(f.get('thickness', 2))},"
            f"{str(f.get('color', 'B')).upper()},{int(f.get('corner_rounding', 0))}^FS")


def _render_circle(f: dict, dpi: int) -> str:
    x, y = _xy(f, dpi)
    d = f.get("diameter_dots") or (mm2dots(f.get("diameter_mm", 0), dpi) if f.get("diameter_mm") else 0)
    d = max(3, min(4095, d))  # ^GC p.212
    t = int(f.get("thickness", 1))
    return f"^FO{x},{y}^GC{d},{t},{str(f.get('color', 'B')).upper()}^FS"


def _xy(f: dict, dpi: int) -> tuple[int, int]:
    """Per-axis: dots win on that axis only, the other axis still honours mm.

    Mixing (x_dots with y_mm) is legitimate -- e.g. centring a barcode needs an
    exact dot offset horizontally while the vertical position is a layout number
    in mm. Treating "x_dots present" as "ignore y" silently dropped the barcode
    to y=0.
    """
    x = int(f["x_dots"]) if "x_dots" in f else mm2dots(f.get("x_mm", 0), dpi)
    y = int(f["y_dots"]) if "y_dots" in f else mm2dots(f.get("y_mm", 0), dpi)
    return x, y


def _xywh(f: dict, dpi: int) -> tuple[int, int, int, int]:
    """Same per-axis rule as _xy (dots win per dimension, mm for the rest)."""
    x, y = _xy(f, dpi)
    w = int(f["width_dots"]) if "width_dots" in f else mm2dots(f.get("width_mm", 0), dpi)
    h = int(f["height_dots"]) if "height_dots" in f else mm2dots(f.get("height_mm", 0), dpi)
    return x, y, w, h


def mm2dots(mm, dpi: int) -> int:
    """mm -> dots at the printhead dpi (1 dot = 1/25.4 mm)."""
    return int(round(float(mm) * dpi / 25.4))


def _is_cjk(s: str) -> bool:
    return any(ord(c) > 0x2E80 for c in str(s))


def build_label(spec: dict) -> tuple[str, list]:
    """Build ZPL II from a label spec. Returns (zpl, issues[]).

    Spec keys (mm for sizes, auto-converted to dots at label['dpi']):
      dpi, width_mm, height_mm, quantity, darkness (^MD), char_encoding (^CI),
      print_speed/slew_speed/backfeed_speed (^PR), mirror (^PM), invert (^PO),
      shift_left (^LS), label_top (^LT), media_tracking {mode, black_mark_offset} (^MN),
      print_mode {mode, media_loss} (^MM), fields: text/barcode/note/box/circle/image/reverse.
    """
    issues: list = []
    dpi = int(spec.get("dpi", 203))
    if dpi not in (152, 200, 203, 300, 600):
        issues.append({"severity": "warning", "code": "DPI", "field": "label",
                       "message": f"dpi {dpi} unusual — Zebra printheads are 6/8/12/24 dots/mm "
                                  f"(152/203/300/600 dpi per ^GB p.210 note)"})
    w_mm, h_mm = float(spec.get("width_mm", 0)), float(spec.get("height_mm", 0))
    pw = spec.get("print_width_dots") or (mm2dots(w_mm, dpi) if w_mm else 0)
    ll = spec.get("label_length_dots") or (mm2dots(h_mm, dpi) if h_mm else 0)
    out = ["^XA"]
    if pw:
        if not (2 <= pw <= PW_MAX):
            issues.append({"severity": "error", "code": "PW", "field": "label",
                           "message": f"print_width {pw} outside 2-{PW_MAX} dots (^PW p.329)"})
        out.append(f"^PW{pw}")
    if ll:
        if not (1 <= ll <= LL_MAX):
            issues.append({"severity": "error", "code": "LL", "field": "label",
                           "message": f"label_length {ll} outside 1-{LL_MAX} dots (^LL p.294)"})
        out.append(f"^LL{ll}")
    ci = spec.get("char_encoding", None)
    text_fields = [f for f in spec.get("fields", []) if str(f.get("type", "text")).lower() in ("text", "barcode")]
    cjk_present = any(_is_cjk(f.get("data", "")) for f in text_fields)
    if cjk_present and ci is None:
        ci = CI_UNICODE  # ^CI28 recommended for Kanji/Unicode (^CI p.157)
        issues.append({"severity": "warning", "code": "CI_CJK", "field": "label",
                       "message": "non-Latin text found -> ^CI28 (Unicode) emitted; make sure a CJK-capable "
                                  "font is active: a resident TrueType via font_file '^A@' (e.g. "
                                  "E:SIMSUN.TTF / E:HANS.TTF on ZR668), font 15/16 on ZT230/ZE500/Xi4/"
                                  "ZM400/RZ600, or a .TTF downloaded via ~DY"})
    if ci is not None:
        out.append(f"^CI{ci}")
    if spec.get("barcode_defaults"):
        b = spec["barcode_defaults"]
        r = b.get("ratio", BY_R)
        ratio = f"{r:g}" if isinstance(r, float) else str(r)
        out.append(f"^BY{int(b.get('module_width', BY_W))},{ratio},{int(b.get('bar_height', BY_H))}")
    if spec.get("default_font"):
        d = spec["default_font"]
        out.append(f"^CF{str(d.get('font', '0'))},{int(d.get('height', CF_H))},{int(d.get('width', CF_W))}")
    if spec.get("default_orientation"):
        o = str(spec["default_orientation"]).upper()
        if o in ORIENTATIONS:
            out.append(f"^FW{o}")
    for key, cmd in (("mirror", "^PM"), ("invert", "^PO")):
        if spec.get(key):
            out.append(cmd + _bool(spec[key], "N"))
    if spec.get("darkness") is not None:
        d = float(spec["darkness"])
        # ~SD sets ABSOLUTE darkness (p.333); ^MD is a relative delta (p.301) that
        # accumulates across jobs — use ~SD for a deterministic 0-30 setting.
        if 0.0 <= d <= 30.0:
            out.append(f"~SD{d:g}")
        else:
            if not (MD_MIN <= d <= MD_MAX):
                issues.append({"severity": "error", "code": "MD", "field": "label",
                               "message": f"darkness {d} outside {MD_MIN}..{MD_MAX} (^MD p.301)"})
            out.append(f"^MD{d:g}")
    if spec.get("print_speed") or spec.get("slew_speed") or spec.get("backfeed_speed"):
        pr = "^PR"
        pr += str(spec.get("print_speed", "2")).upper()
        if spec.get("slew_speed"):
            pr += str(spec["slew_speed"]).upper()
        if spec.get("backfeed_speed"):
            pr += str(spec["backfeed_speed"]).upper()
        out.append(pr)
    if spec.get("shift_left"):
        out.append(f"^LS{int(spec['shift_left'])}")
    if spec.get("label_top") is not None:
        out.append(f"^LT{int(spec['label_top'])}")
    mt = spec.get("media_tracking")
    if mt:
        mode = str(mt.get("mode", "N")).upper()
        if mode not in MN_MODES:
            issues.append({"severity": "error", "code": "MN", "field": "label",
                           "message": f"media_tracking mode '{mode}' not in N/Y/W/M/B/A (^MN p.307)"})
        out.append(f"^MN{mode},{int(mt.get('black_mark_offset', 0))}")
    pm = spec.get("print_mode")
    if pm:
        mode = str(pm.get("mode", "T")).upper()
        if mode not in MM_MODES:
            issues.append({"severity": "error", "code": "MM", "field": "label",
                           "message": f"print_mode '{mode}' not in T/P/R/A/K (^MM p.305)"})
        out.append(f"^MM{mode},{_bool(pm.get('media_loss'), 'N')}")

    used_hex = False
    for f in spec.get("fields", []):
        t = str(f.get("type", "text")).lower()
        if t == "text":
            chunk, uh = _render_text(f, dpi, issues)
        elif t == "barcode":
            chunk, uh = _render_barcode(f, dpi, issues)
        elif t in ("note", "box"):
            chunk, uh = _render_box(f, dpi), False
        elif t == "circle":
            chunk, uh = _render_circle(f, dpi), False
        elif t == "reverse":
            chunk, uh = "^FR", False
        elif t == "image":
            name = f.get("name", "")
            dev = f.get("device", "R")
            chunk, uh = (f"^FO{_xy(f, dpi)[0]},{_xy(f, dpi)[1]}^XGR:{name},1,1^FS" if name else ""), False
            if not name:
                issues.append({"severity": "error", "code": "IMG_NAME", "field": "image",
                               "message": "image field needs 'name' (download first via printer_graphics)"})
        else:
            issues.append({"severity": "error", "code": "FIELD_TYPE", "field": t,
                           "message": f"unknown field type '{t}' (text/barcode/note/box/circle/image/reverse)"})
            continue
        used_hex |= uh
        out.append(chunk)

    q = int(spec.get("quantity", 1))
    if not (1 <= q <= 99999999):
        issues.append({"severity": "error", "code": "PQ", "field": "label",
                       "message": f"quantity {q} outside 1-99999999 (^PQ p.324)"})
    if q != 1:
        out.append(f"^PQ{q}")
    out.append("^XZ")
    zpl = "".join(out)

    # symbology-specific data rules (manual comments + common practice)
    for f in spec.get("fields", []):
        if str(f.get("type", "text")).lower() != "barcode":
            continue
        sym = str(f.get("symbology", "")).lower()
        data = str(f.get("data", ""))
        if sym == "i2of5" and not (data.isdigit() and len(data) % 2 == 0):
            issues.append({"severity": "error", "code": "I2OF5_DATA", "field": sym,
                           "message": "Interleaved 2 of 5 requires an even number of digits (^B2 p.68)"})
        if sym == "upca" and not (data.isdigit() and len(data) in (11, 12)):
            issues.append({"severity": "warning", "code": "UPCA_DATA", "field": sym,
                           "message": "UPC-A wants 11 digits + check digit (12 total) (^B9 p.85)"})
        if sym == "ean13" and not (data.isdigit() and len(data) in (12, 13)):
            issues.append({"severity": "warning", "code": "EAN13_DATA", "field": sym,
                           "message": "EAN-13 wants 12 digits + check digit (13 total) (^BE p.109)"})
        if sym in ("planet", "postnet") and not data.isdigit():
            issues.append({"severity": "warning", "code": "POSTAL_DATA", "field": sym,
                           "message": "postal barcodes take digits only (^B5 p.78 / ^BZ p.150)"})
        if sym == "msi" and not data.isdigit():
            issues.append({"severity": "warning", "code": "MSI_DATA", "field": sym,
                           "message": "MSI barcode takes digits only (^BM p.122)"})
        if sym not in ("qrcode", "datamatrix", "aztec", "pdf417", "micropdf417", "upsmaxicode") and _is_cjk(data):
            issues.append({"severity": "warning", "code": "BC_CJK", "field": sym,
                           "message": f"{sym}: CJK in linear barcode data is not supported; use qrcode/datamatrix"})
        if not used_hex and any(c in data for c in "^~"):
            issues.append({"severity": "warning", "code": "HEX_NEEDED", "field": sym,
                           "message": "field data contains ^ or ~ — hex escaping was disabled "
                                      "(no_hex); those characters terminate the field and were stripped"})
    return zpl, issues


def validate_label(spec: dict) -> list:
    _, issues = build_label(spec)
    return issues


def validate_zpl(zpl: str) -> list:
    """Light validation of raw ZPL against the manual's command set.

    Field data (^FD…) is blanked before the command scan: a label whose data
    contains '^' or '~' (escaped with ^FH, or a customer string) must not be
    reported as "unknown command".
    """
    issues = []
    scan = re.sub(r"\^FD[^\^]*", "^FD", zpl)
    known = set(re.findall(r"[\^~][A-Z0-9@]{1,2}", scan))
    # catalog of documented ^/~ commands from the extracted manual
    catalog = KNOWN_COMMANDS
    if not catalog:
        # Library users may call validate_zpl without ever importing .catalog
        # (the MCP server does it via side effect). Install on first use rather
        # than reporting every legitimate command as UNKNOWN_CMD.
        from . import catalog as _catalog
        catalog = _catalog.install()
    for tok in known:
        if tok not in catalog:
            issues.append({"severity": "warning", "code": "UNKNOWN_CMD", "field": tok,
                           "message": f"{tok} not found in the ZPL II manual's command catalog"})
    # ^BQ p.129: field data must open with <error correction><input mode>,
    for m in re.finditer(r"\^BQ[^\^]*\^FD([^\^]*)", zpl):
        data = m.group(1)
        if not re.match(r"^[HQML][AM],", data):
            issues.append({"severity": "error", "code": "QR_FD_PREFIX", "field": "^BQ",
                           "message": "QR field data must start with <error correction>"
                                      "<input mode>, e.g. ^FDQA,<data> (^BQ p.129) — got "
                                      f"'^FD{data[:12]}'"})
    if zpl.count("^XA") != zpl.count("^XZ"):
        issues.append({"severity": "error", "code": "BRACKETS", "field": "format",
                       "message": "^XA/^XZ unbalanced — every format needs both (^XA p.370 / ^XZ p.375)"})
    if not re.search(r"[\^~][A-Z0-9]", zpl):
        issues.append({"severity": "warning", "code": "NO_CMD", "field": "format",
                       "message": "no ZPL commands found in payload"})
    return issues


# Filled by knowledge.py at import of server; keep a conservative default here.
KNOWN_COMMANDS: set = set()
