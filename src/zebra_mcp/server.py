"""Zebra Printer MCP server — stdio transport.

Tools (all grounded in the ZPL II Programming Guide zpl-zbi2-pg-en-202609
and the Link-OS docs at https://techdocs.zebra.com/link-os/latest/):

  zpl_knowledge        look up ZPL commands, defaults, barcode symbologies, SGD vars
  generate_zpl         build validated ZPL II from a JSON label spec
  validate_zpl         validate raw ZPL against the manual's command catalog
  list_printers        enumerate Windows printer queues
  send_zpl             send ZPL over TCP 9100 / Windows queue / USB queue / preview file
  printer_status       query a Link-OS printer via SGD (! U1 getvar) over TCP
"""
from __future__ import annotations

import json
import re
import socket
import sys

from mcp.server.fastmcp import FastMCP

from . import builder, knowledge, transport
from . import catalog  # noqa: E402  (installs manual catalog into builder.validate_zpl)

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
                    _time.sleep(0.4)
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


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
