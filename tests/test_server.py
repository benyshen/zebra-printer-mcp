"""Shell tests — the MCP package is a thin wrapper over zpl-core.

Run after `pip install -e .` (or with the repo venv):
    python tests/test_server.py

What this asserts, and why it is not the engine test suite:
  * all 9 tools are registered and callable through the FastMCP app;
  * every engine symbol resolves inside zpl_core, not inside this repo —
    a stray copy here is exactly the drift this refactor removed;
  * the tools return the documented JSON shapes.

Engine behaviour (ZPL emission, barcode geometry, render pixels, OCR) is tested
in zpl-core: `python tests/test_engine.py` there.
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import zipfile
from pathlib import Path

import zpl_core

from zebra_mcp import ENGINE_VERSION, __version__, server

FAILED = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("PASS  " if ok else "FAIL  ") + name + (f"  [{detail}]" if detail else ""))
    if not ok:
        FAILED.append(name)


def tool_names() -> list[str]:
    tools = server.mcp._tool_manager._tools  # FastMCP 1.x internal, stable in practice
    return sorted(tools)


# ---------------------------------------------------------------- 包装层职责

check("shell version is 0.4.0", __version__ == "0.4.0", __version__)
check("engine version is reported", ENGINE_VERSION == zpl_core.__version__, ENGINE_VERSION)

expected = sorted(["zpl_knowledge", "generate_zpl", "validate_zpl", "render_zpl",
                   "list_printers", "send_zpl", "printer_status", "print_xlsx",
                   "zpl_from_image"])
check("9 tools registered", tool_names() == expected, ", ".join(tool_names()))

engine_dir = Path(zpl_core.__file__).parent
repo_src = Path(server.__file__).resolve().parents[1]
check("engine resolves inside zpl_core", engine_dir.name == "zpl_core", str(engine_dir))
check("repo holds no engine modules",
      not [p.name for p in repo_src.glob("*.py")
           if p.stem in {"builder", "knowledge", "transport", "render", "photo", "ocr"}],
      str(sorted(p.name for p in repo_src.glob("*.py"))))

# ---------------------------------------------------------------- 工具行为

r = json.loads(server.zpl_knowledge(query="BY"))
check("zpl_knowledge returns the ^BY entry",
      r.get("found") is True and "syntax" in r, str(r)[:80])

r = json.loads(server.zpl_knowledge(mode="barcodes"))
check("zpl_knowledge barcodes mode lists 28 symbologies",
      len(r.get("supported_by_generate_zpl", [])) == 28, str(len(r.get("symbologies", {}))))

spec = {"dpi": 203, "width_mm": 60, "height_mm": 40, "fields": [
    {"type": "text", "x_mm": 3, "y_mm": 3, "height_dots": 30, "data": "SHELL"},
    {"type": "barcode", "symbology": "code128", "x_mm": 3, "y_mm": 12,
     "height_dots": 60, "data": "ABC-123"}]}
r = json.loads(server.generate_zpl(spec))
check("generate_zpl builds a label", r["zpl"].startswith("^XA") and r["issues"] == [],
      f"issues={r['issues']}")
zpl = r["zpl"]

r = json.loads(server.validate_zpl(zpl))
check("validate_zpl accepts the generated label", r.get("ok") is True, str(r)[:80])

out = Path(tempfile.mkdtemp(prefix="mcp_render_")) / "p.png"
r = json.loads(server.render_zpl(zpl, out_path=str(out), scale=2))
check("render_zpl writes a PNG at the requested scale",
      r.get("ok") is True and r.get("scale") == 2 and out.exists(),
      f"{r.get('dots')} dots -> {out.name}")

r = json.loads(server.list_printers())
check("list_printers answers with a list", isinstance(r, list), f"{len(r)} queues")

r = json.loads(server.send_zpl(zpl, transport_kind="preview"))
check("send_zpl preview transport writes a file",
      r.get("ok") is True and r.get("transport") == "preview", str(r)[:70])

r = json.loads(server.generate_zpl({"width_mm": 0}))
check("generate_zpl reports a bad spec instead of raising",
      r.get("ok") is False or "issues" in r, str(r)[:70])

# xlsx tool: build a 2-row workbook with inline strings, preview transport
tmp = Path(tempfile.mkdtemp(prefix="mcp_xlsx_")) / "rows.xlsx"
rows = [("张伟", "A001"), ("李娜", "B002")]
_sheet = "".join(
    f'<row r="{i}"><c r="A{i}" t="inlineStr"><is><t>{n}</t></is></c>'
    f'<c r="B{i}" t="inlineStr"><is><t>{c}</t></is></c></row>'
    for i, (n, c) in enumerate(rows, 1))
with zipfile.ZipFile(tmp, "w") as z:
    z.writestr("[Content_Types].xml",
               '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
               '<Default Extension="xml" ContentType="application/xml"/>'
               '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
               '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
    z.writestr("xl/workbook.xml",
               '<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
               '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1" '
               'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"/></sheets></workbook>')
    z.writestr("xl/worksheets/sheet1.xml",
               '<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
               f'<sheetData>{_sheet}</sheetData></worksheet>')

r = json.loads(server.print_xlsx(str(tmp), skip_header=False, dry_run=True))
check("print_xlsx turns 2 rows into 2 labels", r.get("labels") == 2, str(r)[:110])

# photo tool: fixture from the engine repo, preview only
fixture = Path(__file__).parent / "fixtures" / "photo_sample.png"
if fixture.exists():
    r = json.loads(server.zpl_from_image(str(fixture), width_mm=70, height_mm=70, dry_run=True))
    check("zpl_from_image returns ZPL for a photographed label",
          r.get("zpl", "").startswith("^XA") and r.get("elements"),
          f"{len(r.get('elements') or [])} elements")
else:
    check("zpl_from_image fixture present", False, str(fixture))

print()
if FAILED:
    print(f"{len(FAILED)} CHECK(S) FAILED: {FAILED}")
    raise SystemExit(1)
print("ALL SHELL CHECKS PASSED")
