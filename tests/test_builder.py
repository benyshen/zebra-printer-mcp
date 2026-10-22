"""Builder smoke tests — run: python tests/test_builder.py (from the repo root).

Covers: mm->dot math, ^BY/^PQ/^PW/^LL emission, symbology mapping, auto ^CI28 for
CJK, font_file -> ^A@ TrueType calls, ^FH hex escapes, ^FB field blocks, ^FT origin,
graphics (^GB/^GC/^FR/^XGR), error capture codes, validate_zpl against the manual
command catalog. No printer needed; everything is checked as generated ZPL strings.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from zebra_mcp import builder, catalog, knowledge, transport  # noqa: E402,F401


def show(name, spec):
    zpl, issues = builder.build_label(spec)
    print(f"=== {name} ===")
    print(zpl)
    if issues:
        print("ISSUES:", json.dumps(issues, ensure_ascii=False))
    print()
    return zpl, issues


# 1) 60x40mm 203dpi: Code128 + text, English
z1, i1 = show("code128 label 60x40", {
    "dpi": 203, "width_mm": 60, "height_mm": 40, "quantity": 2,
    "barcode_defaults": {"module_width": 3, "bar_height": 12},
    "fields": [
        {"type": "text", "data": "SKU-2026-001", "x_mm": 3, "y_mm": 3, "size_mm": 4},
        {"type": "barcode", "symbology": "code128", "data": "ABC12345", "x_mm": 3, "y_mm": 12, "height_mm": 15, "interpret": True},
        {"type": "text", "data": "Made in China", "x_mm": 3, "y_mm": 32, "size_mm": 3},
    ],
})
assert z1.startswith("^XA") and z1.endswith("^XZ")
assert "^PW480" in z1 and "^LL320" in z1 and "^PQ2" in z1
assert re.search(r"\^BY3,3,12", z1), z1
assert re.search(r"\^BCN,120", z1), z1
assert "^FO24,24^A0N,32" in z1

# 2) QR code + Chinese text -> auto ^CI28
z2, i2 = show("QR + CJK", {
    "dpi": 300, "width_mm": 50, "height_mm": 50,
    "fields": [
        {"type": "text", "data": "零件编号 A-100", "x_mm": 2, "y_mm": 2, "size_mm": 4},
        {"type": "barcode", "symbology": "qrcode", "data": "https://example.com/p/100", "x_mm": 10, "y_mm": 12, "scale": 8},
    ],
})
assert "^CI28" in z2 and re.search(r"\^BQ", z2)
assert any(i["code"] == "CI_CJK" for i in i2)

# 3) multi symbology
z3, i3 = show("multi-symbology", {
    "dpi": 203, "width_mm": 100, "height_mm": 80,
    "fields": [
        {"type": "barcode", "symbology": "datamatrix", "data": "DM12345", "x_mm": 2, "y_mm": 2, "module_size_dots": 10},
        {"type": "barcode", "symbology": "pdf417", "data": "PDF417DATA", "x_mm": 2, "y_mm": 14, "height_mm": 10, "security_level": 2},
        {"type": "barcode", "symbology": "ean13", "data": "5901234123457", "x_mm": 2, "y_mm": 28},
        {"type": "barcode", "symbology": "upca", "data": "012345678905", "x_mm": 2, "y_mm": 42},
        {"type": "barcode", "symbology": "i2of5", "data": "12345670", "x_mm": 2, "y_mm": 56},
        {"type": "barcode", "symbology": "codabar", "data": "A12345B", "x_mm": 2, "y_mm": 68},
    ],
})
for cmd in ["^BX", "^B7", "^BE", "^B9", "^B2", "^BK"]:
    assert cmd in z3, cmd
assert not [i for i in i3 if i["severity"] == "error"]

# 4) ^FH hex escape
z4, i4 = show("hex escape", {
    "dpi": 203, "width_mm": 50, "height_mm": 20,
    "fields": [{"type": "text", "data": "price: 100~200^OFF", "x_mm": 2, "y_mm": 2, "size_mm": 3}],
})
assert "^FH" in z4 and "_7E" in z4 and "_5E" in z4

# 5) errors captured
z5, i5 = show("error capture", {
    "dpi": 203, "width_mm": 50, "height_mm": 20, "darkness": 99,
    "fields": [
        {"type": "barcode", "symbology": "nope", "data": "X", "x_mm": 2, "y_mm": 2},
        {"type": "barcode", "symbology": "i2of5", "data": "12345", "x_mm": 2, "y_mm": 8},
    ],
})
codes = {i["code"] for i in i5}
assert "BARCODE_UNKNOWN" in codes and "I2OF5_DATA" in codes and "MD" in codes

# 6) mm->dots math
z6, _ = show("mm math", {"dpi": 203, "width_mm": 60, "height_mm": 40, "fields": [{"type": "text", "data": "x", "x_mm": 10, "y_mm": 10}]})
assert "^PW480" in z6 and "^LL320" in z6 and "^FO80,80" in z6

# 7) ^FB between ^A and ^FD
z7, _ = show("field block", {
    "dpi": 203, "width_mm": 60, "height_mm": 30,
    "fields": [{"type": "text", "data": "line1 data line2 data line3", "x_mm": 2, "y_mm": 2,
                "size_mm": 3, "block": {"width_mm": 50, "lines": 3, "line_height": 12, "alignment": "L"}}],
})
assert re.search(r"\^A0N,24\^FB400,3,12,L,0\^FD", z7), z7

# 8) ^FT relative placement
z8, _ = show("FT", {"dpi": 203, "width_mm": 50, "height_mm": 20,
    "fields": [{"type": "text", "data": "AAA", "x_mm": 0, "y_mm": 0, "size_mm": 3, "origin": "FT"}]})
assert "^FT" in z8

# 9) ascii preview renders text
prev = transport.ascii_preview(z1, 60, 40, 203)
print(prev)
assert "SKU" in prev

# 10) validate_zpl
bad = builder.validate_zpl("^XA^QQ^FDhi^FS")
assert any(i["code"] == "UNKNOWN_CMD" for i in bad), bad
zz = builder.validate_zpl("^XA^ZZ0^FS^XZ")
assert not any(i["code"] == "UNKNOWN_CMD" for i in zz), zz  # ^ZZ IS in the manual (diagnostics)
bad2 = builder.validate_zpl("^XA^FO10,10^A0N^FDhi^FS")
assert any(i["code"] == "BRACKETS" for i in bad2)

# 11) catalog
kc = knowledge.known_commands()
for c in ["^XA", "^XZ", "^BY", "^BQ", "^BC", "^FO", "^FD", "^FS", "^PW", "^LL", "^CI", "^MD", "^PR", "^PQ", "^LT", "^FB", "^MN", "^MM", "~DG", "~DY", "^A@"]:
    assert c in kc, c
print("known commands:", len(kc))

# 12) no barcode_defaults -> no ^BY emitted (printer defaults 2,2,10)
z12, _ = show("by defaults", {"dpi": 203, "width_mm": 40, "height_mm": 20,
    "fields": [{"type": "barcode", "symbology": "code128", "data": "1234", "x_mm": 2, "y_mm": 2}]})
assert "^BY" not in z12

# 14) graphics render
z14, i14 = show("graphics", {
    "dpi": 203, "width_mm": 40, "height_mm": 30,
    "fields": [
        {"type": "box", "x_mm": 1, "y_mm": 1, "width_mm": 38, "height_mm": 28, "thickness": 3},
        {"type": "circle", "x_mm": 5, "y_mm": 5, "diameter_mm": 6},
        {"type": "reverse"},
        {"type": "text", "data": "REV", "x_mm": 2, "y_mm": 2, "size_mm": 3},
        {"type": "image", "name": "logo.grf", "x_mm": 20, "y_mm": 5},
    ],
})
assert "^GB" in z14 and "^GC" in z14 and "^FR" in z14 and "^XGR:logo.grf" in z14

# 15) CJK label with font_file -> ^A@ TrueType (verified end-to-end on a ZR668:
# resident E:SIMSUN.TTF / E:HANS.TTF + ^CI28 + UTF-8 bytes printed correctly)
z15, i15 = show("cjk font_file", {
    "dpi": 200, "width_mm": 70, "height_mm": 30, "darkness": 10,
    "fields": [
        {"type": "text", "data": "条码AI一线 宋体", "x_mm": 2, "y_mm": 2, "size_mm": 3.5,
         "font_file": "E:SIMSUN.TTF"},
        {"type": "text", "data": "条码AI一线 思源黑", "x_mm": 2, "y_mm": 9, "size_mm": 3.5,
         "font_file": "E:HANS.TTF"},
        {"type": "text", "data": "默认字体对比 ABC123", "x_mm": 2, "y_mm": 16, "size_mm": 3.5},
        {"type": "barcode", "symbology": "code128", "data": "CJK-TEST-2026", "x_mm": 2, "y_mm": 21, "height_mm": 6},
    ],
})
assert "^CI28" in z15
assert "^A@N,28" in z15 and "E:SIMSUN.TTF" in z15 and "E:HANS.TTF" in z15
assert any(i["code"] == "CI_CJK" for i in i15)

# 16) transport encoding: ^CI28 + non-ASCII must go out as UTF-8 bytes, never cp1252 '?'
from zebra_mcp.transport import _clean_zpl  # noqa: E402
raw = _clean_zpl("^XA^CI28^FD中文^FS^XZ")
assert "中文".encode("utf-8") in raw, raw

print("ALL TESTS PASSED")
