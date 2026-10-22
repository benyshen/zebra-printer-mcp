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
    crashes some Python builds).

    [Console]::OutputEncoding forces UTF-8 so non-ASCII queue names survive the
    trip through powershell.exe on a Chinese/Japanese Windows (default console
    code page would mangle them).
    """
    ps = ("[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
          "Get-Printer | Select-Object Name,PortName,DriverName,PrinterStatus,Shared "
          "| ConvertTo-Json -Compress")
    try:
        r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps],
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
        if not p.get("Name"):        # skip the {"error": ...} entry PowerShell failures return
            continue
        n = str(p["Name"]).lower()
        if q == n or q in n:
            return p
    return None


# --- Windows RAW spooling ------------------------------------------------------
# PowerShell has no cmdlet that spools raw bytes to a named queue (Print-File
# only exists on some hosts with the PrintManagement module, and Out-Printer
# sends text). winspool.drv's WritePrinter with datatype RAW is the documented
# way and works with any ZPL queue, driver-agnostic.
def _spool_raw_windows(printer_name: str, data: bytes, doc_name: str = "zebra-mcp") -> int:
    import ctypes
    from ctypes import wintypes

    class DOC_INFO_1(ctypes.Structure):
        _fields_ = [("pDocName", wintypes.LPWSTR),
                    ("pOutputFile", wintypes.LPWSTR),
                    ("pDatatype", wintypes.LPWSTR)]

    winspool = ctypes.WinDLL("winspool.drv", use_last_error=True)
    winspool.OpenPrinterW.argtypes = [wintypes.LPWSTR, ctypes.POINTER(wintypes.HANDLE),
                                      ctypes.c_void_p]
    winspool.OpenPrinterW.restype = wintypes.BOOL
    winspool.StartDocPrinterW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                          ctypes.POINTER(DOC_INFO_1)]
    winspool.StartDocPrinterW.restype = wintypes.DWORD
    winspool.StartPagePrinter.argtypes = [wintypes.HANDLE]
    winspool.StartPagePrinter.restype = wintypes.BOOL
    winspool.WritePrinter.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                      ctypes.POINTER(wintypes.DWORD)]
    winspool.WritePrinter.restype = wintypes.BOOL
    winspool.EndPagePrinter.argtypes = [wintypes.HANDLE]
    winspool.EndDocPrinter.argtypes = [wintypes.HANDLE]
    winspool.ClosePrinter.argtypes = [wintypes.HANDLE]

    handle = wintypes.HANDLE()
    if not winspool.OpenPrinterW(printer_name, ctypes.byref(handle), None):
        raise OSError(ctypes.get_last_error(), f"OpenPrinter failed for '{printer_name}'")
    try:
        di = DOC_INFO_1(doc_name, None, "RAW")
        job = winspool.StartDocPrinterW(handle, 1, ctypes.byref(di))
        if not job:
            raise OSError(ctypes.get_last_error(), "StartDocPrinter failed")
        try:
            if not winspool.StartPagePrinter(handle):
                raise OSError(ctypes.get_last_error(), "StartPagePrinter failed")
            written = wintypes.DWORD(0)
            buf = ctypes.create_string_buffer(data, len(data))
            if not winspool.WritePrinter(handle, buf, len(data), ctypes.byref(written)):
                raise OSError(ctypes.get_last_error(), "WritePrinter failed")
            winspool.EndPagePrinter(handle)
            return int(job)
        finally:
            winspool.EndDocPrinter(handle)
    finally:
        winspool.ClosePrinter(handle)


def send_windows_printer(printer_name: str, zpl: str) -> dict:
    """Send raw ZPL to a Windows printer queue (RAW datatype, driver-agnostic).

    The queue's driver normally passes RAW jobs straight to the printer, so a
    Zebra ZPL queue prints the label. If the queue is bound to a non-ZPL driver
    (e.g. a PDF/virtual printer) the job is accepted but nothing meaningful is
    printed — the preview file is the ground truth in that case.
    """
    printer = _find_printer(printer_name)
    if printer is None:
        avail = [p["Name"] for p in list_windows_printers() if p.get("Name")]
        return {"ok": False, "transport": "windows_spool", "printer": printer_name,
                "error": "printer not found", "available_printers": avail}
    data = _clean_zpl(zpl)
    path = SCRATCH / f"zpl_job_{os.getpid()}.prn"
    path.write_bytes(data)
    drv = str(printer.get("DriverName", ""))
    zebra_drv = bool(re.search(r"zebra|zd(esigner)?", drv, re.I))
    try:
        job = _spool_raw_windows(str(printer["Name"]), data)
        return {"ok": True, "transport": "windows_spool", "printer": printer["Name"],
                "driver": drv, "zebra_zpl_driver_detected": zebra_drv,
                "datatype": "RAW", "job_id": job, "bytes_sent": len(data),
                "preview": str(path),
                "hint": None if zebra_drv else
                "driver does not look like a Zebra ZPL driver — the queue may rasterize "
                "the RAW job; check the .prn preview or use a TCP raw transport"}
    except Exception as e:
        return {"ok": False, "transport": "windows_spool", "printer": printer["Name"],
                "driver": drv, "error": f"{type(e).__name__}: {e}", "preview": str(path),
                "hint": "RAW spooling needs the queue to accept raw bytes; for Zebra "
                        "printers prefer transport_kind=tcp (port 9100)"}


def send_usb(printer_name: str, zpl: str) -> dict:
    """USB transport = Windows print queue (USB printers are queue-backed on Windows).

    For raw USB without a driver, Windows cannot deliver raw ZPL; the documented
    Link-OS approach is the driver queue or the Zebra SDK. This path uses the
    queue and reports the driver kind.
    """
    printer = _find_printer(printer_name)
    if printer is None:
        avail = [p["Name"] for p in list_windows_printers() if p.get("Name")]
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

    def place(x_dots: int, y_dots: int, text: str):
        cx = min(cols - 1, max(0, x_dots // 6))
        ry = min(rows - 1, max(0, y_dots // 12))
        s = text[: cols - cx]
        for i, ch in enumerate(s):
            grid[ry][cx + i] = ch

    # parameters never contain '^', so [^^]* is a safe "up to the next command"
    # span — this also covers ^A@ TrueType calls (E:SIMSUN.TTF) that the old
    # [^F]* class cut short at the F in .TTF
    for m in re.finditer(r"\^FO(\d+),(\d+)\^(?:A[^^]*)?(?:\^FB[^^]*)?\^FD([^^]*)\^FS", zpl):
        place(int(m.group(1)), int(m.group(2)), m.group(3))
    for m in re.finditer(r"\^FO(\d+),(\d+)\^(?:B[A-Z0-9][^^]*)?(?:\^FH[^F^^]*)?\^FD([^^]*)\^FS", zpl):
        place(int(m.group(1)), int(m.group(2)), "|||||||||||||||||| " + m.group(3))
    out = "+" + "-" * cols + "+"
    for r in grid:
        out += "\n|" + "".join(r).rstrip().ljust(cols) + "|"
    out += "\n+" + "-" * cols + "+"
    return out


def write_preview(zpl: str, name: str = "") -> dict:
    """Write the exact bytes that would be sent to a .zpl file (per-process name
    so concurrent MCP calls do not overwrite each other's preview)."""
    path = SCRATCH / (name or f"label_preview_{os.getpid()}.zpl")
    path.write_bytes(_clean_zpl(zpl))
    return {"preview_file": str(path), "bytes": path.stat().st_size}
