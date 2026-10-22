"""Read .xlsx with the standard library and turn rows into a multi-label ZPL job.

Why no openpyxl: an MCP server should not grow a dependency for "read two string
columns". An .xlsx is a zip of XML -- sharedStrings.xml holds the text, the sheet
holds cell references -- and that is all this needs.

Why one job instead of N calls: `transport.send_tcp` waits ~1 s for the printer's
echo on every send, so 500 labels one-by-one costs 8 minutes of pure waiting.
The whole job is a single ZPL stream (one ^XA..^XZ block per label) sent over one
connection, and the echo is read once at the end.
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path

from . import builder, transport

__all__ = ["read_xlsx_rows", "resolve_column", "plan_specs", "build_job", "code128_width_dots"]


# ---------------------------------------------------------------- xlsx reading

_CELL_RE = re.compile(r'<c r="([A-Z]+)(\d+)"([^>]*)>(.*?)</c>', re.S)
_VAL_RE = re.compile(r"<v>(.*?)</v>", re.S)
_IS_RE = re.compile(r"<is>.*?<t[^>]*>(.*?)</t>.*?</is>", re.S)


def _col_letters_to_index(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def read_xlsx_rows(path: str | Path, sheet: str | int | None = None) -> list[list[str]]:
    """Return the sheet as a list of rows (empty trailing cells trimmed)."""
    path = Path(path)
    with zipfile.ZipFile(path) as z:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            xml = z.read("xl/sharedStrings.xml").decode("utf-8", "replace")
            shared = [re.sub(r"<[^>]+>", "", si).replace("&amp;", "&").replace("&lt;", "<")
                      .replace("&gt;", ">").replace("&quot;", '"').replace("&apos;", "'")
                      for si in re.findall(r"<si>(.*?)</si>", xml, re.S)]

        names = [n for n in z.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", n)]
        if not names:
            raise ValueError(f"{path.name}: no worksheet found")
        if isinstance(sheet, int):
            target = sorted(names)[max(0, sheet - 1)]
        elif isinstance(sheet, str) and sheet:
            target = ""
            book = z.read("xl/workbook.xml").decode("utf-8", "replace")
            rels = z.read("xl/_rels/workbook.xml.rels").decode("utf-8", "replace")
            rid = ""
            for m in re.finditer(r'<sheet[^>]*name="([^"]+)"[^>]*r:id="(rId\d+)"', book):
                if m.group(1) == sheet:
                    rid = m.group(2)
                    break
            if rid:
                mm = re.search(rf'Id="{rid}"[^>]*Target="([^"]+)"', rels)
                if mm:
                    target = "xl/" + mm.group(1).lstrip("/").replace("xl/", "")
            if not target:
                raise ValueError(f"sheet {sheet!r} not found in {path.name}")
        else:
            target = sorted(names)[0]

        xml = z.read(target).decode("utf-8", "replace")
        rows: dict[int, dict[int, str]] = {}
        for m in _CELL_RE.finditer(xml):
            col = _col_letters_to_index(m.group(1))
            row = int(m.group(2))
            attrs, body = m.group(3), m.group(4)
            val = ""
            if 't="s"' in attrs:
                idx = _VAL_RE.search(body)
                if idx:
                    try:
                        val = shared[int(idx.group(1))]
                    except (ValueError, IndexError):
                        val = ""
            elif 't="inlineStr"' in attrs:
                t = _IS_RE.search(body)
                val = re.sub(r"<[^>]+>", "", t.group(1)) if t else ""
            else:
                v = _VAL_RE.search(body)
                if v:
                    raw = v.group(1)
                    if re.fullmatch(r"-?\d+\.0+", raw):
                        raw = raw.split(".")[0]
                    val = raw
            rows.setdefault(row, {})[col] = val.strip()

    if not rows:
        return []
    out = []
    for r in range(1, max(rows) + 1):
        cells = rows.get(r, {})
        width = max(cells) + 1 if cells else 0
        out.append([cells.get(c, "") for c in range(width)])
    while out and not any(out[-1]):
        out.pop()
    return out


def resolve_column(rows: list[list[str]], selector) -> int:
    """Accept 'A', 1-based index, or a header name from the first row."""
    if not rows:
        raise ValueError("empty sheet")
    if isinstance(selector, int):
        return selector - 1
    s = str(selector).strip()
    if re.fullmatch(r"[A-Za-z]{1,3}", s):
        return _col_letters_to_index(s.upper())
    if re.fullmatch(r"\d+", s):
        return int(s) - 1
    for c, cell in enumerate(rows[0]):
        if cell.strip().lower() == s.lower():
            return c
    raise ValueError(f"column {selector!r} not found in header {rows[0]}")


# --------------------------------------------------------------- label layout

def code128_width_dots(data: str, module_width: int = 3) -> int:
    """Code 128 width in dots: start(11) + n*11 + check(11) + stop(13).

    Correct for Code Set B, which is what ASCII 32..126 uses. Digits would also
    encode in set C (2 digits per symbol) but builder emits plain set B, so the
    wider estimate is the right one for centering.
    """
    return (11 * (len(data) + 2) + 13) * max(1, module_width)


def plan_specs(rows: list[list[str]], name_col, id_col, *, width_mm=50.0, height_mm=30.0,
               dpi=203, name_size_mm=4.0, barcode_height_mm=10.0, module_width=3,
               font_file="E:SIMSUN.TTF", skip_header=True) -> list[dict]:
    """Turn table rows into per-label specs. Rows with an empty id are skipped."""
    ni, ii = resolve_column(rows, name_col), resolve_column(rows, id_col)
    body = rows[1:] if skip_header else rows
    specs = []
    for row in body:
        name = row[ni].strip() if ni < len(row) else ""
        emp = row[ii].strip() if ii < len(row) else ""
        if not emp:
            continue
        specs.append({
            "dpi": dpi, "width_mm": width_mm, "height_mm": height_mm,
            "barcode_defaults": {"module_width": module_width},
            "fields": [
                # ^FB with justification C lets the printer centre the name using
                # real font metrics -- no character-width guessing on our side.
                {"type": "text", "data": name, "x_mm": 0, "y_mm": 2.5,
                 "size_mm": name_size_mm, "font_file": font_file,
                 "block": {"width_mm": width_mm, "lines": 1, "alignment": "C"}},
                {"type": "barcode", "symbology": "code128", "data": emp,
                 "x_mm": 0, "y_mm": max(0.5, height_mm - barcode_height_mm - 5.0),
                 "height_mm": barcode_height_mm, "interpret": True},
            ],
        })
        # centre the barcode arithmetically: ZPL has no alignment for ^BC
        pw = builder.mm2dots(width_mm, dpi)
        bw = code128_width_dots(emp, module_width)
        specs[-1]["fields"][1]["x_dots"] = max(0, (pw - bw) // 2)
    return specs


def build_job(specs: list[dict]) -> tuple[str, list]:
    """One ZPL stream, one ^XA..^XZ block per label."""
    blocks, issues = [], []
    for n, spec in enumerate(specs, 1):
        zpl, iss = builder.build_label(spec)
        blocks.append(zpl)
        for i in iss:
            issues.append({**i, "label": n})
    return "".join(blocks), issues


def write_job(zpl: str, path: str | Path) -> Path:
    p = Path(path)
    p.write_bytes(zpl.encode("utf-8"))
    return p
