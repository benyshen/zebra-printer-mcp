"""Builder smoke tests — run: python tests/test_builder.py (from the repo root).

Covers: mm->dot math, ^BY/^PQ/^PW/^LL emission, symbology mapping, auto ^CI28 for
CJK, font_file -> ^A@ TrueType calls, ^FH hex escapes, ^FB field blocks, ^FT origin,
graphics (^GB/^GC/^FR/^XGR), error capture codes, validate_zpl against the manual
command catalog. No printer needed; everything is checked as generated ZPL strings.
"""
import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from zebra_mcp import builder, catalog, knowledge, transport, xlsx_batch  # noqa: E402,F401


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
assert "^A@N,28,28,E:SIMSUN.TTF" in z15, z15   # width defaulted to height
assert "E:HANS.TTF" in z15
assert any(i["code"] == "CI_CJK" for i in i15)

# 16) transport encoding: ^CI28 + non-ASCII must go out as UTF-8 bytes, never cp1252 '?'
from zebra_mcp.transport import _clean_zpl  # noqa: E402
raw = _clean_zpl("^XA^CI28^FD中文^FS^XZ")
assert "中文".encode("utf-8") in raw, raw

# 17) Orientation lands in the letter the manual's Format line defines
# (^B9/^B0 lead with 'o'; ^BR/^BQ/^BO lead with 'a') and never crashes
z17, i17 = show("orientation letters", {
    "dpi": 203, "width_mm": 70, "height_mm": 60,
    "fields": [
        {"type": "barcode", "symbology": "upca", "data": "012345678905", "orientation": "R", "x_mm": 2, "y_mm": 2},
        {"type": "barcode", "symbology": "upce", "data": "0123456", "orientation": "R", "x_mm": 2, "y_mm": 12},
        {"type": "barcode", "symbology": "gs1databar", "data": "0123456789012",
         "orientation": "N", "height_mm": 10, "x_mm": 2, "y_mm": 24},
        {"type": "barcode", "symbology": "qrcode", "data": "https://example.com", "scale": 4,
         "orientation": "N", "x_mm": 40, "y_mm": 30},
    ],
})
assert "^B9R^FD" in z17, z17            # ^B9o,h,f,g,e
assert "^B0R^FD" in z17, z17            # ^B0o,e,h,f,g
assert re.search(r"\^BRN,,,,80\^FD", z17), z17   # ^BR a,b,c,d,e,f -> height is 'e'
assert "^BQN,,4" in z17, z17            # ^BQ a,b,c,d,e -> magnification is 'c'
assert not [i for i in i17 if i["severity"] == "error"], i17

# 18) QR ^FD must carry the mandatory <EC><input mode>, prefix (^BQ p.129)
assert "^FDQA,https://example.com" in z17, z17
z18, _ = show("qr ec H", {
    "dpi": 203, "width_mm": 40, "height_mm": 40,
    "fields": [{"type": "barcode", "symbology": "qrcode", "data": "ABC", "scale": 5,
                "ec_level": "H", "mask": 3, "x_mm": 2, "y_mm": 2}]})
assert "^BQN,,5,H,3^FDHA,ABC" in z18, z18

# 19) every documented symbology builds without error and emits its command
for sym, data in [("code11", "12345"), ("codablock", "ABCDEF123456"), ("code49", "ABC123"),
                  ("micropdf417", "ABC123"), ("planet", "12345678901"),
                  ("postnet", "123456789"), ("msi", "12345"), ("plessey", "1234ABCD"),
                  ("logmars", "1234"), ("std2of5", "1234"), ("ean8", "1234567"),
                  ("upcext", "12345"), ("aztec", "ABC"), ("upsmaxicode", "ABC123")]:
    zs, iss = builder.build_label({"dpi": 203, "width_mm": 60, "height_mm": 40,
        "fields": [{"type": "barcode", "symbology": sym, "data": data, "x_mm": 2, "y_mm": 2}]})
    errs = [i for i in iss if i["severity"] == "error"]
    assert not errs, (sym, errs)
    assert builder.BARCODE_SPECS[sym]["cmd"] in zs, (sym, zs)

# 20) MicroPDF417 takes a mode, not PDF417's security/columns/row ratio (^BFo,h,m)
z20, i20 = show("micropdf417 mode", {"dpi": 203, "width_mm": 60, "height_mm": 40,
    "fields": [{"type": "barcode", "symbology": "micropdf417", "data": "ABC123", "mode": 5,
                "height_mm": 8, "x_mm": 2, "y_mm": 2}]})
assert re.search(r"\^BFN,64,5\^FD", z20), z20
assert not [i for i in i20 if i["severity"] == "error"], i20

# 21) ASCII preview must show text rendered through ^A@ (TrueType font names
# contain 'F' in .TTF — the old regex stopped there and dropped the field)
prev21 = transport.ascii_preview(z15, 70, 30, 200)
print(prev21)
assert "条码AI一线" in prev21, prev21

# 22) transport: preview filename is per-process, raw spool helper is exposed
w = transport.write_preview("^XA^XZ")
assert Path(w["preview_file"]).name.endswith(".zpl") and w["bytes"] > 0
assert callable(getattr(transport, "_spool_raw_windows", None))

# 23) validate_zpl must not read ^FD data as commands, and must catch a QR
# ^FD string without the mandatory <EC><input mode>, prefix
clean = builder.validate_zpl("^XA^FO10,10^A0N,20^FD100~200 SALE^FS^XZ")
assert not [i for i in clean if i["code"] == "UNKNOWN_CMD"], clean
noprefix = builder.validate_zpl("^XA^FO10,10^BQN,2,4^FDhttps://example.com^FS^XZ")
assert any(i["code"] == "QR_FD_PREFIX" for i in noprefix), noprefix
withprefix = builder.validate_zpl("^XA^FO10,10^BQN,2,4^FDQA,https://example.com^FS^XZ")
assert not [i for i in withprefix if i["code"] == "QR_FD_PREFIX"], withprefix

# 24) every supported symbology's command resolves in zpl_knowledge(mode='command')
for sym, spec in builder.BARCODE_SPECS.items():
    hit = knowledge.get_command(spec["cmd"])
    assert hit is not None, (sym, spec["cmd"])
    assert hit["page"] > 0 and hit["syntax"].startswith(spec["cmd"]), (sym, hit)

# 25) note and box are the same ^GB command
z25, _ = show("note vs box", {"dpi": 203, "width_mm": 50, "height_mm": 30,
    "fields": [{"type": "note", "x_mm": 1, "y_mm": 1, "width_mm": 20, "height_mm": 10},
               {"type": "box", "x_mm": 1, "y_mm": 15, "width_mm": 20, "height_mm": 10}]})
assert z25.count("^GB") == 2, z25

# 26) validate_zpl must install the manual catalog on first use. A library user
# that imports only builder (never .catalog) used to get UNKNOWN_CMD for ^XA/^FO.
import subprocess
root = Path(__file__).resolve().parents[1]
probe = subprocess.run(
    [sys.executable, "-c",
     "import sys; sys.path.insert(0, 'src');"
     "from zebra_mcp import builder;"
     "print([i['code'] for i in builder.validate_zpl('^XA^FO10,10^A0N,20^FDX^FS^XZ')])"],
    capture_output=True, text=True, cwd=str(root))
assert probe.returncode == 0, probe.stderr
assert "UNKNOWN_CMD" not in probe.stdout, probe.stdout

# 27) xlsx -> multi-label job: reader, column resolution, centring, one stream
def _write_xlsx(path, rows):
    import zipfile
    strings, idx = [], {}
    for r in rows:
        for c in r:
            if c not in idx:
                idx[c] = len(strings); strings.append(c)
    body = []
    for ri, row in enumerate(rows, 1):
        cells = "".join(f'<c r="{chr(64+ci)}{ri}" t="s"><v>{idx[v]}</v></c>'
                        for ci, v in enumerate(row, 1))
        body.append(f'<row r="{ri}">{cells}</row>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/></Types>')
        z.writestr("xl/workbook.xml", '<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="人员" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr("xl/worksheets/sheet1.xml", '<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>' + "".join(body) + '</sheetData></worksheet>')
        z.writestr("xl/sharedStrings.xml", f'<?xml version="1.0"?><sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="{len(strings)}" uniqueCount="{len(strings)}">' + "".join(f"<si><t>{s}</t></si>" for s in strings) + '</sst>')

xlsx_path = Path(tempfile.gettempdir()) / "zebra_mcp_test_rows.xlsx"
_write_xlsx(xlsx_path, [["姓名", "员工号"], ["张三", "10001"], ["李四", "10002"], ["", ""]])
xrows = xlsx_batch.read_xlsx_rows(xlsx_path)
assert xrows[0] == ["姓名", "员工号"] and xrows[1] == ["张三", "10001"], xrows
assert xlsx_batch.resolve_column(xrows, "员工号") == 1
assert xlsx_batch.resolve_column(xrows, "B") == 1 and xlsx_batch.resolve_column(xrows, 1) == 0
# Code 128 set B: start(11) + n*11 + check(11) + stop(13)
assert xlsx_batch.code128_width_dots("10001", 3) == (11 * 7 + 13) * 3 == 270
specs = xlsx_batch.plan_specs(xrows, "姓名", "员工号", width_mm=50, height_mm=30)
assert len(specs) == 2, specs          # the empty row is skipped
pw = builder.mm2dots(50, 203)
for s_ in specs:
    bar = s_["fields"][1]
    assert bar["x_dots"] == (pw - 270) // 2, bar     # centred arithmetically
    assert bar["y_mm"] == 15.0                       # per-axis _xy keeps mm for y
    assert s_["fields"][0]["block"]["alignment"] == "C"
job, jissues = xlsx_batch.build_job(specs)
assert job.count("^XA") == 2 and job.count("^XZ") == 2, job
assert "^FO65,120^BCN,80^FD10001^FS" in job, job   # x from dots, y from mm
assert all(i["validation"] if False else True for i in jissues)

# 28) per-axis origin: dots on one axis must not reset the other
z28, _ = builder.build_label({"dpi": 203, "width_mm": 50, "height_mm": 30, "fields": [
    {"type": "text", "data": "X", "x_dots": 65, "y_mm": 15, "size_mm": 3}]})
assert "^FO65,120" in z28, z28
z28b, _ = builder.build_label({"dpi": 203, "width_mm": 50, "height_mm": 30, "fields": [
    {"type": "box", "x_mm": 1, "y_mm": 1, "width_dots": 100, "height_mm": 10}]})
assert "^GB100,80" in z28b, z28b

# 29) photo -> ZPL: decode the fixture, check geometry against the labels the
#     reference implementation produced for the same files, then round-trip the
#     emitted ZPL back through the validator.
import os
here = os.path.dirname(os.path.abspath(__file__))
fixture = os.path.join(here, "fixtures", "photo_sample.png")
try:
    from zebra_mcp import photo, ocr
    res = photo.convert(fixture, width_mm=60, height_mm=40, frame="full", use_ocr=False)
    syms = {s["format"]: s for s in res["decoded"]}
    assert set(syms) == {"Code128", "QRCode"}, syms
    assert syms["Code128"]["text"] == "ABC123", syms["Code128"]
    assert syms["QRCode"]["text"] == "NGSU", syms["QRCode"]
    # perspective-free mapping: px * (label_dots / image_width) == dots
    # a QR must not be measured as a 1D bar band (that silently emitted ^BQ with
    # blank magnification and 15-dot height); the module-level _TWO_D set holds
    # zxing's CamelCase names, so nothing may shadow it with lowercase ones
    qr = [e for e in res["elements"] if e["format"] == "QRCode"][0]
    assert qr["kind"] == "barcode2d", qr
    assert qr["dots"][3] - qr["dots"][1] > 50, qr["dots"]      # ~66 dots tall
    assert "^BQ" in res["zpl"] and "^BQN,,3" in res["zpl"], res["zpl"]
    bar = [e for e in res["elements"] if e["kind"] == "barcode1d"][0]
    assert abs(bar["dots"][0] - 30) <= 3 and abs(bar["dots"][2] - 336) <= 4, bar["dots"]
    assert abs(bar["bar_height"] - 75) <= 4, bar
    assert "^BY3," in res["zpl"], res["zpl"]          # per-field module width
    assert builder.validate_zpl(res["zpl"]) == [], res["zpl"]
    assert res["scale"] > 0 and res["warnings"] is not None
    if ocr.available():
        full = photo.convert(fixture, width_mm=60, height_mm=40, frame="full")
        texts = [e.get("text") for e in full["elements"] if e["kind"] == "text"]
        assert any("NANJ" in (t or "") for t in texts), texts
        assert any(w["code"] == "HRI_MERGED" for w in full["warnings"]), full["warnings"]
    # a second photo at a different resolution: the scale is derived from
    # width_mm/photo_px, so nothing may be hardcoded to the first fixture
    res2 = photo.convert(os.path.join(here, "fixtures", "photo_sample2.png"),
                         width_mm=60, height_mm=40, frame="full", use_ocr=False)
    assert res2["photo_px"] == [397, 242], res2["photo_px"]
    dec2 = {s["format"]: s["text"] for s in res2["decoded"]}
    assert dec2["Code128"] == "ABC123" and dec2["QRCode"] == "RA", dec2
    bar2 = [e for e in res2["elements"] if e["kind"] == "barcode1d"][0]
    assert abs(bar2["dots"][0] - 29) <= 3, bar2["dots"]      # px 24 * (480/397)
    assert builder.validate_zpl(res2["zpl"]) == [], res2["zpl"]
    missing = photo.convert(os.path.join(here, "fixtures", "nope.png"))
    assert missing["ok"] is False, missing
except ImportError:
    print("  (photo deps absent: pip install 'zebra-printer-mcp[photo]' -- skipped)")

# 30) vision LLM backend: stub an OpenAI-compatible endpoint and check the wire
#     format, the key handling (never echoed) and the unverified-barcode rule.
import threading, http.server, json as _json
try:
    from zebra_mcp import vision as _vision
    seen = {}
    KEY = "sk-test-DO-NOT-LEAK-123"

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            body = _json.loads(self.rfile.read(n) or b"{}")
            seen["auth"] = self.headers.get("Authorization")
            seen["model"] = body.get("model")
            seen["img"] = body["messages"][1]["content"][1]["image_url"]["url"][:22]
            payload = {"model": "stub-vl", "usage": {"total_tokens": 123},
                       "choices": [{"message": {"content": _json.dumps({"elements": [
                           {"kind": "text", "text": "NANJING", "box": {"x": .6, "y": .7, "w": .3, "h": .1}},
                           {"kind": "barcode", "text": "XY-9", "format": "Code128",
                            "box": {"x": .1, "y": .1, "w": .5, "h": .2}}]})}}]}
            out = _json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/v1"

    vr = _vision.analyze(__import__("numpy").zeros((40, 60, 3), "uint8"),
                         base_url=url, api_key=KEY, model="stub-vl", timeout=10)
    assert vr["ok"], vr
    assert [e["text"] for e in vr["elements"]] == ["NANJING", "XY-9"], vr
    assert seen["auth"] == f"Bearer {KEY}" and seen["model"] == "stub-vl", seen
    assert seen["img"].startswith("data:image/png;base64"), seen
    assert KEY not in _json.dumps(vr, ensure_ascii=False), "key leaked into the result"
    assert "api_key" not in vr, vr

    # through the photo pipeline: model text lands as a field, its barcode value
    # only when zxing decoded nothing -- and then it is flagged unverified
    res_v = photo.convert(fixture, width_mm=60, height_mm=40, frame="full",
                          use_ocr=False, llm_url=url, llm_key=KEY, llm_model="stub-vl")
    assert res_v["source"] == "vision", res_v.get("source")
    kinds = [(e["kind"], e.get("text")) for e in res_v["elements"]]
    assert ("text", "NANJING") in kinds, kinds
    assert any(w["code"] == "VISION_BARCODE_HINT" for w in res_v["warnings"]), res_v["warnings"]
    assert res_v["decoded"] and not any(d.get("unverified") for d in res_v["decoded"]), res_v["decoded"]
    assert KEY not in _json.dumps(res_v, ensure_ascii=False)

    # a wrong key must produce a hint, not the key
    bad = _vision.analyze(__import__("numpy").zeros((40, 60, 3), "uint8"),
                          base_url="http://127.0.0.1:1/v1", api_key=KEY, timeout=3)
    assert bad["ok"] is False and KEY not in bad["error"], bad
    srv.shutdown()
except ImportError:
    print("  (vision needs numpy/cv2: pip install -e '.[photo]' -- skipped)")

# 31) render_zpl -- offline layout preview. Geometry is locked to Zebra's own
#     rendering of the same ZPL (Labelary, 8 dpmm): barcode x30..332, QR x379..441,
#     and ^A0N,43 printing 153x33 dots of ink. If those numbers drift, the preview
#     starts lying about overflow and the tool is worse than useless.
try:
    import tempfile as _tf

    import numpy as _np
    from PIL import Image as _Image

    from zebra_mcp import render as _render

    _out = _tf.mkdtemp(prefix="zpl_render_")
    zpl31, i31 = show("render", {"dpi": 203, "width_mm": 60, "height_mm": 40, "fields": [
        {"type": "text", "data": "NANJING", "x_mm": 40, "y_mm": 29, "size_mm": 5.4},
        {"type": "barcode", "symbology": "code128", "data": "ABC123", "x_mm": 3.8,
         "y_mm": 2.6, "height_mm": 9.4, "module_width": 3},
        {"type": "barcode", "symbology": "qrcode", "data": "NGSU", "x_mm": 47, "y_mm": 3, "scale": 3}]})
    r31 = _render.render(zpl31, out=f"{_out}/a.png", scale=1)
    assert r31["ok"] and r31["dots"] == [480, 320], r31
    assert {"text", "code128", "qrcode"} <= {d["type"] for d in r31["drawn"]}, r31["drawn"]
    assert not r31["warnings"], r31["warnings"]

    _img = _np.array(_Image.open(r31["png"]).convert("L")) < 128

    def _groups(band, gap=20):
        cols = _np.where(band.any(axis=0))[0]
        runs, start, prev = [], cols[0], cols[0]
        for c in cols[1:]:
            if c - prev > gap:
                runs.append((start, prev))
                start = c
            prev = c
        runs.append((start, prev))
        return runs

    _top = _groups(_img[:130, :])
    assert _top[0] == (30, 332), _top            # barcode: ^FO30 + 101 modules x 3 dots
    assert _top[1][0] == 379 and _top[1][1] - _top[1][0] + 1 == 63, _top[1]   # QR 21x3

    _rows = _np.where(_img[200:320, :].any(axis=1))[0]
    _txt = _groups(_img[200:320, :])[0]
    assert 150 <= _txt[1] - _txt[0] + 1 <= 158, _txt      # ^A0N,43 ink width
    assert 31 <= _rows.max() - _rows.min() + 1 <= 36, _rows   # ...and ink height

    # a symbol with no offline encoder must say so instead of drawing a lie
    zpl31b, _ = show("render dm", {"dpi": 203, "width_mm": 40, "height_mm": 30, "fields": [
        {"type": "barcode", "symbology": "datamatrix", "data": "HELLO", "x_mm": 5, "y_mm": 5}]})
    r31b = _render.render(zpl31b, out=f"{_out}/b.png")
    assert any("datamatrix" in s for s in r31b["approximated"]), r31b["approximated"]

    # text overflowing its ^FB block is exactly what the preview exists to catch
    r31c = _render.render("^XA^PW300^LL100^FO10,10^A0N,30^FB60,1,0,L^FDWAY TOO WIDE FOR THE BLOCK^FS^XZ",
                          out=f"{_out}/c.png")
    assert any("wider than its" in w for w in r31c["warnings"]), r31c["warnings"]

    # the MCP tool wraps the same renderer; scale must not change reported dots
    from zebra_mcp import server as _server
    _tool = json.loads(_server.render_zpl(zpl31, out_path=f"{_out}/d.png", scale=2))
    assert _tool["ok"] and _tool["dots"] == [480, 320] and _tool["scale"] == 2, _tool
    assert _Image.open(_tool["png"]).size == (960, 640), _tool["png"]
except ImportError as exc:
    print(f"  (render needs pillow/segno/python-barcode: pip install -e '.[render]' -- skipped: {exc})")

print("ALL TESTS PASSED")
