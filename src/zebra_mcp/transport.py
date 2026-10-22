"""Print transports for ZPL output: TCP 9100, USB printer queue, Windows spooler, file preview.

Raw ZPL over TCP/USB reaches the printer's ZPL interpreter directly (the
Link-OS way). Sending to a Windows print queue works only when the queue
driver is a Zebra ZPL driver — otherwise the job is treated as a document and
the printer prints it as text, so we always return a rendering preview too.
"""
from __future__ import annotations

import os
import re
import socket
import subprocess
import tempfile
import unicodedata
from pathlib import Path

SCRATCH = Path(os.environ.get("ZEBRA_MCP_OUT", Path(tempfile.gettempdir()) / "zebra-mcp"))
SCRATCH.mkdir(parents=True, exist_ok=True)

TCP_DEFAULT_PORT = 9100  # Link-OS/Zebra 9100 raw port


def _clean_zpl(zpl: str, encoding: str = "") -> bytes:
    """Encode ZPL text to bytes.

    Auto: ^CI28/^CI29/^CI30 + non-ASCII -> UTF-8 bytes (raw ^FD data is printed
    through as-is; a ^CI28 label with a TrueType CJK font such as
    E:SIMSUN.TTF needs the UTF-8 codepoints on the wire, cp1252 would replace
    them with '?'). Otherwise cp1252 as before. encoding= overrides.
    """
    enc = encoding or ""
    if not enc:
        if re.search(r"\^CI(?:28|29|30)\b", zpl) and any(ord(c) > 127 for c in zpl):
            enc = "utf-8"
        else:
            enc = "cp1252"
    try:
        return zpl.encode(enc, errors="replace")
    except LookupError:
        return zpl.encode("cp1252", errors="replace")


def send_tcp(host: str, zpl: str, port: int = TCP_DEFAULT_PORT, timeout: float = 5.0) -> dict:
    """Send raw ZPL to an Ethernet/Wi-Fi Zebra printer on the 9100 port."""
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            data = _clean_zpl(zpl)
            s.sendall(data)
            # Zebra 9100 usually does not echo; wait briefly for possible error text
            s.settimeout(1.0)
            resp = b""
            try:
                while True:
                    chunk = s.recv(4096)
                    if not chunk:
                        break
                    resp += chunk
                    if len(resp) > 4096:
                        break
            except socket.timeout:
                pass
        return {"ok": True, "transport": f"tcp://{host}:{port}",
                "bytes_sent": len(data),
                "response": resp.decode("cp1252", "replace").strip() or None,
                "note": "no ^XA error echo received" if not resp else None}
    except OSError as e:
        return {"ok": False, "transport": f"tcp://{host}:{port}",
                "error": f"{type(e).__name__}: {e}",
                "hint": "check printer IP (get it with printer_status sgd 'device.ip.address'), "
                        "port 9100 (Zebra default), and that the host can reach the printer"}


def list_windows_printers() -> list[dict]:
    """Enumerate local Windows printers with PowerShell (stable; ctypes winspool
    crashes some Python builds)."""
    ps = ("Get-Printer | Select-Object Name,PortName,DriverName,PrinterStatus,Shared "
          "| ConvertTo-Json -Compress")
    try:
        r = subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps],
                           capture_output=True, timeout=30)
        import json
        out = r.stdout.decode("utf-8", "replace").strip()
        if not out:
            return []
        data = json.loads(out)
        if isinstance(data, dict):
            data = [data]
        return data
    except Exception as e:
        return [{"error": f"{type(e).__name__}: {e}"}]


def _find_printer(name_query: str) -> dict | None:
    printers = list_windows_printers()
    q = name_query.lower()
    for p in printers:
        n = str(p.get("Name", "")).lower()
        if q == n or q in n:
            return p
    return None


def send_windows_printer(printer_name: str, zpl: str) -> dict:
    """Send ZPL to a Windows printer queue.

    Works when the queue uses a Zebra ZPL driver. With a non-Zebra driver the
    job is accepted but rendered as garbage text — the preview file is the
    ground truth in that case.
    """
    printer = _find_printer(printer_name)
    if printer is None:
        avail = [p.get("Name") for p in list_windows_printers() if isinstance(p, dict)]
        return {"ok": False, "transport": "windows_spool", "printer": printer_name,
                "error": "printer not found", "available_printers": avail}
    path = SCRATCH / f"zpl_job_{os.getpid()}.prn"
    path.write_bytes(_clean_zpl(zpl))
    drv = str(printer.get("DriverName", ""))
    zebra_drv = bool(re.search(r"zebra|zDesigner|ZebraDesigner", drv, re.I))
    cmd = f'Print-File -Path "{path}" -PrinterName "{printer["Name"]}"' if False else \
          f'Get-Content -Encoding Byte -TotalCount 0 "{path}" | Out-Null; ' \
          f'Start-Process -FilePath "{path}" -Verb Print -PassThru | Out-Null'
    # Print-File needs the Print to a PDF Monitor style file association; more
    # reliable: copy to the printer via PowerShell Spooler API is overkill —
    # use the documented Print-File cmdlet (PS 5+: available via PrintManagement)
    ps = f'Print-File -Path "{path}" -PrinterName "{printer["Name"]}"'
    try:
        r = subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps],
                           capture_output=True, timeout=60)
        ok = r.returncode == 0
        return {"ok": ok, "transport": "windows_spool",
                "printer": printer["Name"], "driver": drv,
                "zebra_zpl_driver_detected": zebra_drv,
                "stderr": r.stderr.decode("utf-8", "replace").strip() or None,
                "preview": str(path),
                "hint": None if zebra_drv else
                "driver is not a Zebra ZPL driver — the print queue will rasterize "
                "the ZPL as text; preview the .prn or use a TCP/USB raw transport"}
    except Exception as e:
        return {"ok": False, "transport": "windows_spool", "printer": printer["Name"],
                "error": f"{type(e).__name__}: {e}", "preview": str(path)}


def send_usb(printer_name: str, zpl: str) -> dict:
    """USB transport = Windows print queue (USB printers are queue-backed on Windows).

    For raw USB without a driver, Windows cannot deliver raw ZPL; the documented
    Link-OS approach is the driver queue or the Zebra SDK. This path uses the
    queue and reports the driver kind.
    """
    printer = _find_printer(printer_name)
    if printer is None:
        avail = [p.get("Name") for p in list_windows_printers()]
        return {"ok": False, "transport": "usb", "printer": printer_name,
                "error": "USB printer queue not found",
                "available_printers": avail,
                "hint": "install the Zebra Windows driver first; USB ZPL printing goes "
                        "through the Windows queue"}
    res = send_windows_printer(printer["Name"], zpl)
    res["transport"] = "usb"
    return res


def ascii_preview(zpl: str, width_mm: float = 100, height_mm: float = 50,
                  dpi: int = 203) -> str:
    """Rough text-mode preview of a ZPL label (for chat display without a printer).

    Parses ^FO/^A/^FD/^FS and barcode data blocks into a monospace approximation.
    """
    w_dots = max(1, int(width_mm * dpi / 25.4))
    h_dots = max(1, int(height_mm * dpi / 25.4))
    cols = max(20, w_dots // 6)
    rows = max(6, h_dots // 12)
    grid = [[" "] * cols for _ in range(rows)]

    def place(x_dots: int, y_dots: int, text: str, big: bool = False):
        cx = min(cols - 1, max(0, x_dots // 6))
        ry = min(rows - 1, max(0, y_dots // 12))
        s = text[: cols - cx]
        for i, ch in enumerate(s):
            grid[ry][cx + i] = ch

    for m in re.finditer(r"\^FO(\d+),(\d+)\^(?:A[0-9A-Z][^F]*)?(?:\^FB[^F]*)?\^FD([^\x5e]*)\^FS", zpl):
        place(int(m.group(1)), int(m.group(2)), m.group(3), True)
    for m in re.finditer(r"\^FO(\d+),(\d+)\^(?:B[A-Z0-9][^F]*)?\^FD([^\x5e]*)\^FS", zpl):
        place(int(m.group(1)), int(m.group(2)), "|||||||||||||||||| " + m.group(3))
    out = "+" + "-" * cols + "+"
    for r in grid:
        out += "\n|" + "".join(r).rstrip().ljust(cols) + "|"
    out += "\n+" + "-" * cols + "+"
    return out


def write_preview(zpl: str, name: str = "label_preview.zpl") -> dict:
    path = SCRATCH / name
    path.write_bytes(_clean_zpl(zpl))
    return {"preview_file": str(path), "bytes": path.stat().st_size}
