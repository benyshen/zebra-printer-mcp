"""Zebra Printer MCP server — stdio transport.

Tools (all grounded in the ZPL II Programming Guide zpl-zbi2-pg-en-202609
and the Link-OS docs at https://techdocs.zebra.com/link-os/latest/):

  zpl_knowledge        look up ZPL commands, defaults, barcode symbologies, SGD vars
  generate_zpl         build validated ZPL II from a JSON label spec
  validate_zpl         validate raw ZPL against the manual's command catalog
  list_printers        enumerate Windows printer queues
  send_zpl             send ZPL over TCP 9100 / Windows queue / USB queue / preview file
  printer_status       query a Link-OS printer via SGD (! U1 getvar) over TCP
  print_xlsx           one label per .xlsx row (text column + Code 128 column, centred)
  zpl_from_image       photo of a label -> ZPL, via the local label-reprint reference chain
"""
from __future__ import annotations

import json
import os
import re
import socket
import sys

from mcp.server.fastmcp import FastMCP

from . import builder, knowledge, photo, transport, xlsx_batch
from . import catalog  # noqa: E402  (installs manual catalog into builder.validate_zpl)

# FastMCP in MCP SDK 1.x takes no version kwarg; serverInfo.version therefore
# reports the SDK's own version (1.30.0 here), while the package version lives
# in zebra_mcp.__version__ (0.1.1). Documented as such in docs/.
mcp = FastMCP(
    "zebra-printer",
    instructions=(
        "Zebra label printing. Generate ZPL with generate_zpl (mm sizes auto-converted "
        "to dots; defaults and ranges follow the ZPL II manual). Send with send_zpl "
        "(tcp: printer IP port 9100; windows/usb: a Windows queue with a Zebra driver). "
        "Query printers with printer_status (SGD). Look up commands with zpl_knowledge."
    ),
)


def _json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)


CRLF = chr(13) + chr(10)  # SGD / native status line terminator


@mcp.tool()
def zpl_knowledge(mode: str = "command", query: str = "") -> str:
    """Look up ZPL II / Link-OS knowledge grounded in the ZPL Programming Guide.

    mode:
      command   — exact command reference (query='^BY', '^BQ', '~DG', ...) incl. syntax,
                  defaults, ranges, manual page number
      search    — regex search over all command notes (query='darkness|speed')
      barcodes  — supported barcode symbologies with ZPL commands and data rules
      sgd       — Link-OS SGD setvar/getvar/do examples (query optional filter)
      status    — ZBI II status/error message codes (query optional filter)
      list      — every documented command token
    """
    mode = mode.lower()
    if mode == "command":
        if not query:
            return _json({"error": "command mode needs query, e.g. '^BY'"})
        hit = knowledge.get_command(query if query.startswith(("^", "~")) else "^" + query)
        if hit is None:
            near = knowledge.search(query.lstrip("^~"), limit=8)
            return _json({"found": False, "similar": near,
                          "hint": "try mode='search' or mode='list'"})
        return _json({"found": True, **hit})
    if mode == "search":
        return _json({"results": knowledge.search(query or ".", limit=25)})
    if mode == "barcodes":
        return _json({"symbologies": knowledge.BARCODE_NOTES,
                      "supported_by_generate_zpl": sorted(builder.BARCODE_SPECS),
                      "global_defaults": {"^BY": "width 2 dots, ratio 3.0, height 10 dots (manual p.148)"}})
    if mode == "sgd":
        items = {k: v for k, v in knowledge.SGD_COMMANDS.items()
                 if not query or query.lower() in k.lower()}
        return _json({"sgd": items,
                      "note": "SGD runs outside ^XA/^XZ over any raw socket (TCP 9100). "
                              "Use printer_status tool to execute getvar queries live."})
    if mode == "status":
        items = {k: v for k, v in knowledge.STATUS_NOTES.items()
                 if not query or query.lower() in (k + v.get("name", "")).lower()}
        return _json({"status_codes": items})
    if mode == "list":
        return _json(sorted(knowledge.known_commands()))
    return _json({"error": f"unknown mode '{mode}' (command/search/barcodes/sgd/status/list)"})


@mcp.tool()
def generate_zpl(label_spec: dict) -> str:
    """Build ZPL II from a JSON label spec. Returns {zpl, issues, preview_file, ascii_preview}.

    label_spec (sizes in mm unless *_dots):
      dpi (152/203/300/600), width_mm, height_mm, quantity, darkness (-30..30),
      char_encoding (^CI; auto ^CI28 when CJK text present),
      print_speed/slew_speed/backfeed_speed, mirror, invert, label_top,
      media_tracking {mode N/Y/W/M/B/A}, print_mode {mode T/P/R/A/K},
      barcode_defaults {module_width, ratio, bar_height} (^BY),
      default_font {font, height, width} (^CF),
      fields: [
        {type:'text', data, x_mm|x_dots, y_mm|y_dots, font '0-9A-Z', size_mm,
         orientation N/R/I/B, origin FO|FT, block:{width_mm,lines,line_height,alignment L/C/R/J}},
        {type:'barcode', symbology, data, height_mm, interpret, ...per-symbology options},
        {type:'note'|'box', x_mm,y_mm,width_mm,height_mm,thickness},
        {type:'circle', x_mm,y_mm,diameter_mm,thickness},
        {type:'reverse'},
        {type:'image', name, device R|E}  # download first with printer_graphics
      ]
    symbologies: code128 code39 code93 i2of5 planet postnet usps upsmaxicode pdf417
      micropdf417 codabar msi plessey logmars std2of5 ean13 ean8 upca upce upcext
      gs1databar code49 codablock datamatrix qrcode aztec code11
    """
    zpl, issues = builder.build_label(label_spec)
    res = {"zpl": zpl, "issues": issues}
    if zpl:
        res["preview_file"] = transport.write_preview(zpl)["preview_file"]
        res["ascii_preview"] = transport.ascii_preview(
            zpl, float(label_spec.get("width_mm") or 100), float(label_spec.get("height_mm") or 50),
            int(label_spec.get("dpi") or 203))
    return _json(res)


@mcp.tool()
def validate_zpl(zpl: str) -> str:
    """Validate raw ZPL against the ZPL II manual (command catalog + ^XA/^XZ pairing)."""
    issues = builder.validate_zpl(zpl)
    return _json({"issues": issues, "ok": not any(i["severity"] == "error" for i in issues)})


@mcp.tool()
def list_printers() -> str:
    """List Windows printer queues (name/port/driver/status) to find a Zebra queue."""
    return _json(transport.list_windows_printers())


@mcp.tool()
def send_zpl(zpl: str, transport_kind: str = "tcp", host: str = "", printer: str = "",
             port: int = 9100, dry_run: bool = False) -> str:
    """Send ZPL to a printer.

    transport_kind:
      tcp      — host=printer IP, port=9100 (Link-OS raw ZPL port; default)
      windows  — printer=Windows queue name (needs a Zebra ZPL driver)
      usb      — printer=USB-backed Windows queue name
      preview  — only write a preview file (no printer needed; also always returned)
    dry_run=true validates and previews without sending.
    """
    val_issues = builder.validate_zpl(zpl)
    base = {"validation": val_issues}
    if dry_run:
        base["preview_file"] = transport.write_preview(zpl)["preview_file"]
        base["dry_run"] = True
        return _json(base)
    t = transport_kind.lower()
    if t == "tcp":
        if not host:
            return _json({**base, "error": "tcp transport needs host=printer IP"})
        res = transport.send_tcp(host, zpl, port)
    elif t == "windows":
        if not printer:
            return _json({**base, "error": "windows transport needs printer=queue name",
                          "available": transport.list_windows_printers()})
        res = transport.send_windows_printer(printer, zpl)
    elif t == "usb":
        if not printer:
            return _json({**base, "error": "usb transport needs printer=USB queue name",
                          "available": transport.list_windows_printers()})
        res = transport.send_usb(printer, zpl)
    elif t == "preview":
        res = {"ok": True, "transport": "preview", **transport.write_preview(zpl)}
    else:
        return _json({**base, "error": f"unknown transport '{transport_kind}' (tcp/windows/usb/preview)"})
    res.update(base)
    return _json(res)


@mcp.tool()
def printer_status(host: str, port: int = 9100, sgd: str = "", timeout: float = 5.0) -> str:
    """Query a Link-OS Zebra printer via SGD over the raw socket.

    sgd examples:
      getvar $~                          (dump all config)
      getvar device.ip.address
      getvar $device.serial_number
      getvar printer.width.print
      do:calibration
      list
    Empty sgd runs '! U1 getvar "$device.name.custom"' + '! U1 getvar "device.ip.address"'
    plus a ZBI II status poll (^HS) as a reachability test.
    """
    cmds = []
    if sgd:
        s = sgd.strip()
        if s.lower() == "list":
            cmds = ["! U1 getvar \"$~\""]
        elif s.lower().startswith("!"):
            cmds = [s if s.endswith("\r\n") else s + "\r\n"]
        elif s.lower().startswith("do:"):
            cmds = [f"! U1 {s}\r\n"]
        else:
            name = s.lstrip("$")
            cmds = [f"! U1 getvar \"{name}\"\r\n"]
    else:
        cmds = [
            "! U1 getvar \"$device.name.custom\"\r\n",
            "! U1 getvar \"device.ip.address\"\r\n",
            "! U1 getvar \"$printer.width.print\"\r\n",
            "! U1 getvar \"$printer.feed.dimension.label.length\"\r\n",
        ]
    results = []
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            for c in cmds:
                s.sendall(c.encode("ascii"))
                s.settimeout(timeout)
                buf = b""
                while True:
                    try:
                        chunk = s.recv(4096)
                    except socket.timeout:
                        break
                    if not chunk:
                        break
                    buf += chunk
                    if len(buf) >= 65536:
                        break
                    # an SGD getvar reply may be several lines (getvar "$~" is
                    # hundreds): keep reading until the printer goes quiet
                    s.settimeout(0.6)
                results.append({"sgd": c.strip(), "response": buf.decode("utf-8", "replace").strip()})
        # native ZPL status fallback (~HI / ~HS): older Fusion-era firmware
        # (e.g. ZR668 V86.x, pre-Link-OS) replies "?" to SGD getvar
        native: dict = {}
        ident: dict = {}
        status: dict | None = None
        try:
            import time as _time
            with socket.create_connection((host, port), timeout=timeout) as s:
                for tag in ("~HI", "~HS"):
                    s.sendall(f"{tag}{CRLF}".encode("ascii"))
                    _time.sleep(0.15)
                    s.settimeout(timeout)
                    buf = b""
                    while True:
                        try:
                            chunk = s.recv(4096)
                        except socket.timeout:
                            break
                        if not chunk:
                            break
                        buf += chunk
                        # data arrived -> switch to a short idle window, otherwise
                        # every status call pays the full socket timeout (~5 s x 2)
                        s.settimeout(0.5)
                    native[tag] = buf.decode("utf-8", "replace").strip().strip("\x02\x03")
            bits = native.get("~HI", "").split(",")
            if bits and bits[0]:
                ident = {"printer": bits[0]}
                if len(bits) > 1: ident["firmware"] = bits[1]
                if len(bits) > 2: ident["dots_per_mm"] = bits[2]
                if len(bits) > 3: ident["memory"] = bits[3]
            # ~HS is three STX/ETX-framed lines; the last one opens with the
            # four-digit status word (0000 = ready, 0002 = head open, ...)
            for line in reversed(native.get("~HS", "").split(CRLF)):
                m = re.match(r"^(\d{4})\b", line.strip().strip("\x02\x03, "))
                if m:
                    code = m.group(1)
                    status = {"code": code, **knowledge.STATUS_NOTES.get(
                        code, {"name": "UNKNOWN STATUS WORD", "level": "warn"})}
                    break
        except OSError as e:
            native["error"] = f"{type(e).__name__}: {e}"
        sgd_ok = any(r["response"].strip().strip('"') not in ("", "?") for r in results)
        ok = bool(native.get("~HI")) or sgd_ok
        note = None
        if native.get("~HI") and not sgd_ok:
            note = "SGD getvar not supported on this firmware (pre-Link-OS); native ~HI/~HS used instead"
        return _json({"ok": ok, "host": host, "port": port, "results": results,
                      "identification": ident, "status": status, "native_status": native,
                      "sgd_supported": sgd_ok, "note": note})
    except OSError as e:
        return _json({"ok": False, "host": host, "port": port,
                      "error": f"{type(e).__name__}: {e}",
                      "hint": "find the printer IP via list_printers/Windows or the printer LCD; "
                              "SGD needs the raw 9100 port (Link-OS)"})


@mcp.tool()
def print_xlsx(xlsx_path: str, name_column: str = "A", id_column: str = "B",
               sheet: str = "", host: str = "", port: int = 9100,
               width_mm: float = 50.0, height_mm: float = 30.0,
               name_size_mm: float = 4.0, barcode_height_mm: float = 10.0,
               module_width: int = 3, skip_header: bool = True,
               max_labels: int = 2000, dry_run: bool = False) -> str:
    """Print one label per row of an .xlsx: a text column and a Code 128 column, both centred.

    Columns are given as an Excel letter ("A", "C"), a 1-based index ("2"), or a
    header name ("姓名"). The first row is treated as a header unless
    skip_header=false. Rows whose id cell is empty are skipped.

    Layout: the name is centred by the printer itself (^FB ... C, real font
    metrics, needs a CJK-capable font such as E:SIMSUN.TTF for Chinese); the
    barcode is centred arithmetically (^BC has no alignment), so its module
    width must match the real data — Code 128 in set B is 11*(n+2)+13 modules.

    All labels go out as ONE ZPL stream over a single TCP connection: the printer
    echo is read once instead of once per label (5 labels = 1.0 s, not 5 s).

    Returns {labels, rows_read, skipped, bytes, issues[], zpl_file, first_label,
    sent:{ok,transport,...}}. dry_run=true writes the .zpl and stops.
    """
    if not xlsx_path.lower().endswith((".xlsx", ".xlsm")):
        return _json({"error": f"{xlsx_path}: only .xlsx/.xlsm is supported (not .xls)"})
    try:
        rows = xlsx_batch.read_xlsx_rows(xlsx_path, sheet or None)
    except FileNotFoundError:
        return _json({"error": f"file not found: {xlsx_path}"})
    except Exception as exc:
        return _json({"error": f"{type(exc).__name__}: {exc}"})
    if not rows:
        return _json({"error": "sheet is empty"})

    specs = xlsx_batch.plan_specs(
        rows, name_column, id_column, width_mm=width_mm, height_mm=height_mm,
        name_size_mm=name_size_mm, barcode_height_mm=barcode_height_mm,
        module_width=module_width, skip_header=skip_header)
    if not specs:
        return _json({"error": f"no printable rows (id column {id_column!r} is empty everywhere)"})
    if len(specs) > max_labels:
        return _json({"error": f"{len(specs)} labels exceeds max_labels={max_labels}; "
                               "raise it explicitly if that is intended"})

    job, issues = xlsx_batch.build_job(specs)
    zpl_file = str(xlsx_batch.write_job(job, transport.SCRATCH / f"xlsx_job_{os.getpid()}.zpl"))
    base = {"labels": len(specs), "rows_read": len(rows), "columns": {"name": name_column, "id": id_column},
            "bytes": len(job.encode("utf-8")), "issues": issues, "zpl_file": zpl_file,
            "first_label": job.split("^XZ")[0] + "^XZ"}
    if dry_run or not host:
        base["dry_run"] = True
        base["hint"] = "pass host=<printer IP> to actually print over TCP"
        return _json(base)
    base["sent"] = transport.send_tcp(host, job, port)
    return _json(base)


@mcp.tool()
def zpl_from_image(image_path: str, width_mm: float = 60.0, height_mm: float = 40.0,
                   dpi: int = 203, frame: str = "auto", use_ocr: bool = True,
                   font_file: str = "E:SIMSUN.TTF", host: str = "", port: int = 9100,
                   dry_run: bool = True, llm_url: str = "", llm_key: str = "",
                   llm_model: str = "", vision_timeout: float = 60.0) -> str:
    """Turn a photo of an existing label into ZPL (and optionally print it).

    Reconstruction, not design: symbols are decoded (zxing-cpp), text is read
    (PP-OCRv4), and every element is mapped from pixels to dots. Review the
    element list once — a photo cannot tell fonts, and logos/bitmaps are out of
    scope (no ~DG/^GF here).

    image_path : .png/.jpg of the label. frame="auto" tries perspective
                 correction (needs the label on a contrasting background) and
                 falls back to the whole photo; frame="full" when already cropped.
    width_mm/height_mm : the physical label size — this is what turns pixels into
                 dots. A wrong size scales every element; the ASPECT warning
                 fires when the photo and label agree badly.
    use_ocr    : false to return symbols only (no text).
    llm_url / llm_key / llm_model : optional vision LLM (OpenAI-compatible). Empty
                 values fall back to ZEBRA_VISION_URL / ZEBRA_VISION_KEY /
                 ZEBRA_VISION_MODEL. Pass them as arguments when the client can
                 hold a key, or set the environment variables once at registration
                 (README shows both). The key is used in one request header and is
                 never echoed, logged, or written to disk. Barcode content still
                 comes from zxing -- model-supplied values are marked
                 BARCODE_UNVERIFIED.

    Returns {ok, zpl, elements[], warnings[], issues[], validation, zpl_file, sent?}.
    A 1D symbol needing under 2 dots per module is widened with a warning: at
    203dpi thinner bars get misread (Code 128 comes back as Code 39).
    """
    try:
        res = photo.convert(image_path, width_mm=width_mm, height_mm=height_mm,
                            dpi=dpi, frame=frame, use_ocr=use_ocr, font_file=font_file,
                            llm_url=llm_url, llm_key=llm_key, llm_model=llm_model,
                            vision_timeout=vision_timeout)
    except FileNotFoundError:
        return _json({"error": f"image not found: {image_path}"})
    except Exception as exc:
        return _json({"error": f"{type(exc).__name__}: {exc}"})
    zpl = res["zpl"]
    res["validation"] = builder.validate_zpl(zpl)
    res["zpl_file"] = transport.write_preview(zpl, name=f"photo_{os.getpid()}.zpl")["preview_file"]
    if host and not dry_run:
        res["sent"] = transport.send_tcp(host, zpl, port)
    elif host:
        res["note"] = "dry_run=true, nothing sent; pass dry_run=false to print"
    return _json(res)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
