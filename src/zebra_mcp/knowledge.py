"""ZPL II / Link-OS knowledge base.

Built from the extracted ZPL II Programming Guide text (data/zpl_manual_full.txt,
from zpl-zbi2-pg-en-202609.pdf). The command catalog is parsed from the manual's
own 'ZPL Commands' index so validate_zpl() flags only real manual commands; the
SGD list combines variables that appear in the manual with a curated set of
Link-OS SGD names (techdocs.zebra.com/link-os).
"""
from __future__ import annotations

import re
from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / "data"
MANUAL_TXT = DATA / "zpl_manual_full.txt"

# ---------------------------------------------------------------------------
# Command catalog parsed from the manual text
# ---------------------------------------------------------------------------

_CMD_RE = re.compile(r"[~^][A-Z0-9@]{1,2}")  # '@' keeps ^A@ (Scalable TrueType) in the catalog

# Commands introduced in the manual (headings like "ZPL Commands ^XY" or
# "ZPL System Commands ~XY"). Fallback: the set of all ^/~ 1-2 char tokens.
_KNOWN: set[str] | None = None
CATALOG_TXT = DATA / "command_catalog.txt"  # static export; regenerate with scripts/extract_manual.py


def known_commands() -> set[str]:
    global _KNOWN
    if _KNOWN is not None:
        return _KNOWN
    _KNOWN = set()
    # preferred: the static catalog (repo ships this file; the full manual text
    # is a copyrighted extraction and is NOT committed — see README restore table)
    try:
        _KNOWN = {t for t in CATALOG_TXT.read_text(encoding="utf-8").split() if t}
        if _KNOWN:
            return _KNOWN
    except OSError:
        pass
    # fallback: parse the extracted manual text if it happens to be on disk
    try:
        text = MANUAL_TXT.read_text(encoding="utf-8", errors="replace")
        _KNOWN = set(_CMD_RE.findall(text))
    except OSError:
        pass
    if not _KNOWN:
        _KNOWN = {f"^{c}" for c in "ABCDEFGILMNOPQRSTUVWXZ"} | {f"~{c}" for c in "DEGHILNPRYZ"}
    return _KNOWN


# ---------------------------------------------------------------------------
# Command knowledge (verified against data/zpl_params_notes.txt, manual pages)
# ---------------------------------------------------------------------------

COMMAND_NOTES: dict[str, dict] = {
    "^XA": {"page": 370, "name": "Start Format", "syntax": "^XA",
            "notes": "Begins a label format. All format commands must lie between ^XA and ^XZ."},
    "^XZ": {"page": 375, "name": "End Format", "syntax": "^XZ",
            "notes": "Ends the format begun by ^XA and starts printing."},
    "^FO": {"page": 201, "name": "Field Origin", "syntax": "^FO x, y",
            "defaults": {"x": 0, "y": 0}, "range": "0-32000 dots",
            "notes": "Absolute position on the label, in dots, from the top-left corner (or the origin set by ^LH/^OM)."},
    "^FT": {"page": 205, "name": "Field Origin, Text Position", "syntax": "^FT x, y",
            "defaults": {"x": 0, "y": 0}, "range": "0-32000 dots",
            "notes": "Position relative to the end of the previous text field — use for flowing text fields."},
    "^A": {"page": 60, "name": "Scalable font", "syntax": "^A font, [height, [width]]",
           "defaults": {"font": "0", "height": 24, "width": 24}, "range": "height/width 10-32000 dots; font 0-9 A-Z",
           "notes": "Scalable bitmapped fonts; font A-Z are downloaded fonts (or TrueType via ^CWF). ^A0 is the default."},
    "^CF": {"page": 154, "name": "Set Default Font", "syntax": "^CF font, [height, [width]]",
            "defaults": {"font": 0}, "notes": "Default for all following text fields."},
    "^FD": {"page": 190, "name": "Field Data", "syntax": "^FD data",
            "notes": "Prints data. ^, ~, ^FS etc. terminate the field; hex-escape them with ^FH (default indicator _) or use ^FH<indicator>."},
    "^FS": {"page": 251, "name": "Field Terminator", "syntax": "^FS",
            "notes": "Ends a field and sends it to the print buffer."},
    "^FH": {"page": 193, "name": "Hex Escape On", "syntax": "^FH [indicator]",
            "defaults": {"indicator": "_"}, "notes": "Enables hex escaping in field data: _5F=_, _5E=^, _7E=~. Indicator can be any printable char except ^ ~ and alphanumerics."},
    "^BC": {"page": 94, "name": "Code 128", "syntax": "^BC [orientation, [height, [interpret, [check digit, [mode]]]]]",
            "defaults": {"orientation": "N", "interpret": "Y", "check digit": "N", "mode": "N"},
            "range": "height 1-32000 dots; orientation N/R/I/B",
            "notes": "Mode U forces uppercase; mode A optimizes data encoding. Height default comes from ^BY h."},
    "^BQ": {"page": 128, "name": "QR Code", "syntax": "^BQ [model, [magnification size, [error correction]]]",
            "defaults": {"model": 2, "magnification": "0 (auto)", "error correction": "Q"},
            "range": "model 1/2; magnification 1-100; error correction H/Q/M/L",
            "notes": "^FW orientation does not rotate ^BQ. Data is encoded per QR standards."},
    "^BX": {"page": 144, "name": "Data Matrix", "syntax": "^BX [orientation, [quality, [columns, rows]]]",
            "defaults": {"orientation": "N", "quality": 200, "columns": 0, "rows": 1},
            "range": "quality 0/50/80/100/140/200; columns 1-44 (144 for large); rows 1-44 (144)",
            "notes": "ECC200 recommended for industrial use; quality sets minimum data size."},
    "^B7": {"page": 79, "name": "PDF417", "syntax": "^B7 [orientation, [height, [security level, [columns, [row height ratio, [truncate]]]]]]",
            "defaults": {"height": 0, "security level": 0, "columns": 1, "row height ratio": 3, "truncate": "N"},
            "range": "security level 0-8; row height ratio 0-1000",
            "notes": "columns=1 means auto-size; height 0 derives from ^BY."},
    "^B2": {"page": 68, "name": "Interleaved 2 of 5", "syntax": "^B2 [orientation, [height, [interpret, [check digit, [required count, [print count]]]]]]",
            "defaults": {"interpret": "Y", "check digit": "N", "count": "N"},
            "notes": "Data must be an even number of digits."},
    "^BY": {"page": 148, "name": "Bar Code Default Parameters", "syntax": "^BY [width, [ratio, [height]]]",
            "defaults": {"width": 2, "ratio": 3.0, "height": 10},
            "range": "width 1-10 dots; ratio 2.0-3.0 in 0.1 increments (no effect on fixed-ratio symbologies); height 1-32000 dots",
            "notes": "Global defaults for all following barcodes unless overridden on the barcode command. Power-up initial w=2, h=10."},
    "^PW": {"page": 329, "name": "Print Width", "syntax": "^PW width",
            "defaults": {"width": 406}, "range": "2-32000 dots",
            "notes": "Dots across the label (print-head width direction). 406 = 50 mm at 203 dpi; 832 = 104 mm."},
    "^LL": {"page": 294, "name": "Label Length", "syntax": "^LL length",
            "defaults": {"length": 0}, "range": "1-32000 dots",
            "notes": "Media length in dots along the feed direction."},
    "^LH": {"page": 213, "name": "Label Home", "syntax": "^LH x, y", "defaults": {"x": 0, "y": 0},
            "notes": "Sets the field-origin offset for the whole label."},
    "^PM": {"page": 318, "name": "Mirror Print", "syntax": "^PM Y|N", "defaults": {"value": "N"},
            "notes": "Flips the image left-right for iron-on transfers."},
    "^PO": {"page": 308, "name": "Invert Print", "syntax": "^PO Y|N", "defaults": {"value": "N"},
            "notes": "Reverses black/white (white on black)."},
    "^MD": {"page": 301, "name": "Set Print Darkness", "syntax": "^MD value",
            "defaults": {"value": 0}, "range": "-30 to +30 (or 0-5 depending on model)",
            "notes": "Adjusts print darkness from the printer default; positive darkens."},
    "^PR": {"page": 262, "name": "Print Rate", "syntax": "^PR print speed [slew speed [backfeed speed]]",
            "defaults": {"print speed": 2, "slew speed": 2, "backfeed speed": 2},
            "range": "model dependent (e.g. 1-6 ips on many desktop printers), 1=slowest",
            "notes": "Three optional speeds in inches per second; slew speed affects barcode skew."},
    "^PQ": {"page": 324, "name": "Set Print Quantity", "syntax": "^PQ qty, [pause, [after]]",
            "defaults": {"qty": 1, "pause": 0, "after": 0}, "range": "qty 1-99999999",
            "notes": "Prints the whole format N times. pause/after control a separate quantity reset."},
    "^LT": {"page": 335, "name": "Label Top", "syntax": "^LT offset", "defaults": {"offset": 0},
            "range": "0-1000 dots",
            "notes": "Feeds the top of the label a distance before printing (position compensation)."},
    "^MN": {"page": 307, "name": "Media Tracking", "syntax": "^MN mode, [black mark offset]",
            "defaults": {"mode": "N", "offset": 0}, "range": "mode N/Y/W/M/B/A",
            "notes": "N=continuous, Y=web/label gap, M=mark with next-line offset, B=black mark."},
    "^MM": {"page": 305, "name": "Print Method", "syntax": "^MM mode, [media loss]",
            "defaults": {"mode": "T", "media loss": "N"}, "range": "mode T/P/R/A/K (Tear/Cut/Rewind/Auto/Knitpick)",
            "notes": "Method of presenting printed labels."},
    "^FB": {"page": 186, "name": "Field Block", "syntax": "^FB width, [lines, [line height, [alignment, [hanging indent]]]]",
            "defaults": {"lines": 1, "line height": 0, "alignment": "L", "hanging indent": 0},
            "range": "lines 1-9999; alignment L/C/R/J",
            "notes": "Word-wrap field data to width (dots) at the ^FO/^FT position; sits between ^A and ^FD."},
    "^FR": {"page": 236, "name": "Field Reverse", "syntax": "^FR",
            "notes": "Invert the field (white on black) until ^FS or the next ^FR."},
    "^CI": {"page": 155, "name": "Code Page Identifier", "syntax": "^CI codepage",
            "defaults": {"codepage": 437},
            "range": "437,850,1252 (Latin1), 936 (Simplified Chinese GB2312), 950 (Traditional Chinese Big5), "
                     "1250-1257 (Windows regional), 28 (Unicode, ZPL II mode); 737 Greek, 865 Nordic etc.",
            "notes": "Single-byte pages remap bytes 128-255 to glyphs in the ROM fonts. CJK printing: send UTF-8 "
                     "bytes with ^CI28 and call a TrueType CJK font by name via ^A@ — e.g. ZR668 ships resident "
                     "E:SIMSUN.TTF (SimSun) and E:HANS.TTF in flash (verified on ZR668-200dpi V86.20.21Z); ^CI936 "
                     "with GB2312/GBK bytes also works with fonts 15/16 (ZT230, ZE500, Xi4, ZM400 etc.). "
                     "UTF-8/UTF-16 bytes can also be sent directly with ^CI28 on most Link-OS printers."},
    "^A@": {"page": 62, "name": "Scalable/Downloadable Font by Name", "syntax": "^A@ orientation, [height, [width, [drive:font.ext]]]",
            "defaults": {"orientation": "N", "drive": "R:"},
            "range": "drive R/E/B/A; ext .FNT .TTF .TTE; h/w 10-32000 dots (scalable)",
            "notes": "Calls a font by full name, e.g. ^A@N,28,28,E:SIMSUN.TTF. The chosen font carries over to "
                     "subsequent ^A@ without a name. Resident flash fonts listed via the printer web /dir page "
                     "or ~DY directory query. Pair with ^CI28 (UTF-8) for CJK."},
    "^GB": {"page": 210, "name": "Graphic Box", "syntax": "^GB width, [height, [thickness, [color, [corner rounding]]]]",
           "defaults": {"width": 1, "height": 1, "thickness": 1, "color": "B"}, "range": "1-32000 dots",
           "notes": "Border box (thickness 0 = solid fill); colors B black, W white, G gray (models dependent)."},
    "^GC": {"page": 212, "name": "Graphic Circle", "syntax": "^GC diameter, [thickness, [color]]",
           "defaults": {"diameter": 1, "thickness": 1, "color": "B"}, "range": "3-4095 dots",
           "notes": "Circle border (or filled when thickness = radius)."},
    "^LS": {"page": 322, "name": "Shift Left", "syntax": "^LS shift", "defaults": {"shift": 0},
            "range": "0-999 dots", "notes": "Global horizontal print shift (position alignment)."},
    "^FW": {"page": 280, "name": "Field Orientation", "syntax": "^FW N|R|I|B", "defaults": {"orientation": "N"},
            "notes": "Default field rotation for following text/barcode fields (some 2D codes ignore it)."},
    "^LH": {"page": 213, "name": "Label Home", "syntax": "^LH x, y", "defaults": {"x": 0, "y": 0},
            "notes": "Global offset applied to ^FO field origins."},
    "^OM": {"page": 298, "name": "Origin Set", "syntax": "^OM value", "defaults": {"value": 0},
            "notes": "Field-origin origin mode (0=upper left, 1=lower left, 2=lower right, 3=upper right)."},
    "^XG": {"page": 372, "name": "Graphic Field (stored)", "syntax": "^XG name, [color, [magnification]]",
            "notes": "Recalls a stored .GRF image (~DG download, device E/R)."},
    "~DG": {"page": 444, "name": "Download Graphics", "syntax": "~DGname.ext, format, byte count, data",
           "notes": "Downloads a bitmap to printer memory (E=erase after print, R=store). ASCII hex encoding, row format default."},
    "~DY": {"page": 434, "name": "Download Data", "syntax": "~DYname.ext, format, device, byte count, data",
           "notes": "Downloads fonts (.FNT/.TTF), images, formats (.FMT) or ZPL II macros to E/R/B memory."},
    "^CWF": {"page": 159, "name": "Downloaded Font", "syntax": "^CWF name.ext",
            "notes": "Uses a downloaded scalable font as the default font."},
    "^SB": {"page": 249, "name": "Set Media Position", "syntax": "^SB value", "defaults": {"value": 0},
            "range": "0-1000 dots", "notes": "Media position adjustment for mis-registration."},
    "^MDF": {"page": 343, "name": "Media Feed Adjustment", "syntax": "^MDF value", "defaults": {"value": 0},
             "range": "0-32000 dots", "notes": "Distance to advance media before printing the next label."},
    "^MC": {"page": 316, "name": "Media Count", "syntax": "^MC",
            "notes": "Counts labels for media-loss tracking with ^MM media loss."},
    "^SC": {"page": 244, "name": "Scale Field", "syntax": "^SC x, y", "defaults": {"x": 1.0, "y": 1.0},
            "range": "0.1-10", "notes": "Scale the next field by multipliers."},
    "^DE": {"page": 347, "name": "Delete Element", "syntax": "^DE device", "defaults": {"device": "E"},
            "notes": "Deletes stored formats (B=buffer, E=EPROM). Run outside ^XA/^XZ."},
    "^DS": {"page": 350, "name": "Directory List", "syntax": "^DS",
            "notes": "Lists stored files in printer memory. Run outside ^XA/^XZ."},
    "^DF": {"page": 352, "name": "Delete File", "syntax": "^DF name", "notes": "Deletes one stored file."},
    "^DD": {"page": 351, "name": "Download Format", "syntax": "^DD name",
            "notes": "Begins storing a format to memory; ends with ^END. Run outside ^XA/^XZ."},
    "^CD": {"page": 346, "name": "Capture Format", "syntax": "^CD name, [device]",
            "notes": "Stores the format buffer as a named .FMT (device E/R)."},
    "^PR": {"page": 262, "name": "Print Rate", "syntax": "^PR print speed [slew speed [backfeed speed]]"},
}

BARCODE_NOTES: dict[str, dict] = {
    "code128": {"cmd": "^BC", "page": 94, "data": "ASCII; auto check digit unless e=N set", "notes": "subsets A/B/C auto; mode A optimizes"},
    "code39": {"cmd": "^B3", "page": 70, "data": "0-9 A-Z - . $ / + % space", "notes": "check digit optional"},
    "code93": {"cmd": "^BA", "page": 87, "data": "full ASCII", "notes": ""},
    "i2of5": {"cmd": "^B2", "page": 68, "data": "digits, even count", "notes": "count option for required length"},
    "planet": {"cmd": "^B5", "page": 78, "data": "digits", "notes": "USPS legacy postal"},
    "postnet": {"cmd": "^BZ", "page": 150, "data": "digits", "notes": "USPS legacy; t=0 postnet"},
    "usps": {"cmd": "^BZ", "page": 150, "data": "digits incl. check", "notes": "t=3 IM barcode"},
    "upsmaxicode": {"cmd": "^BD", "page": 106, "data": "62-char sets by mode", "notes": "optically read, not scanned"},
    "pdf417": {"cmd": "^B7", "page": 79, "data": "full ASCII", "notes": "security 0-8, auto columns"},
    "micropdf417": {"cmd": "^BF", "page": 111, "data": "full ASCII",
                    "notes": "Format ^BFo,h,m — m is the mode 0-33, not PDF417's s/c/r"},
    "codabar": {"cmd": "^BK", "page": 118, "data": "0-9 - . , $ / + A-D", "notes": "start/stop A-D"},
    "msi": {"cmd": "^BM", "page": 122, "data": "digits", "notes": "check modes A/B/C/D"},
    "plessey": {"cmd": "^BP", "page": 126, "data": "0-9 A-F", "notes": ""},
    "logmars": {"cmd": "^BL", "page": 120, "data": "Code 39 subset, uppercased; Mod 43 check digit",
                "notes": "Format ^BLo,h,g — DoD/LOGMARS application of Code 39; g is the line ABOVE the symbol, there is no f"},
    "industrial2of5": {"cmd": "^BI", "page": 114, "data": "digits", "notes": "continuous numeric, not self-checking"},
    "std2of5": {"cmd": "^BJ", "page": 116, "data": "digits", "notes": ""},
    "ean13": {"cmd": "^BE", "page": 109, "data": "12 digits + check", "notes": ""},
    "ean8": {"cmd": "^B8", "page": 83, "data": "7 digits + check", "notes": ""},
    "upca": {"cmd": "^B9", "page": 85, "data": "11 digits + check", "notes": ""},
    "upce": {"cmd": "^B0", "page": 66, "data": "6 digits + number system", "notes": "expanded by printer"},
    "upcext": {"cmd": "^BS", "page": 137, "data": "2 or 5 digits", "notes": "ISBN/ISSN add-on"},
    "gs1databar": {"cmd": "^BR", "page": 135, "data": "GTIN/AI based",
                    "notes": "Format ^BRa,b,c,d,e,f: a=orientation, b=symbology type 1-12 (default 1), c=magnification, d=separator height, e=barcode height, f=segment width"},
    "code49": {"cmd": "^B4", "page": 74, "data": "0-9 $+./- ",
               "notes": "Format ^B4o,h,f,m: f = interpretation N/A/B, m = mode"},
    "code11": {"cmd": "^B1", "page": 72, "data": "digits and -", "notes": "check digit 1 or 2 (e=Y/N)"},
    "codablock": {"cmd": "^BB", "page": 99, "data": "varies by mode A/B/C", "notes": "stacked 2D"},
    "datamatrix": {"cmd": "^BX", "page": 144, "data": "full ASCII/numeric",
                   "notes": "ECC200; quality 0-200. GS1 DataMatrix = this symbology with FNC1: prefix ^FD data with >8 (manual: 'FNC1 is invoked by >8', inserted just before the AIs)"},
    "qrcode": {"cmd": "^BQ", "page": 128, "data": "numeric/alphanumeric/byte/Kanji",
               "notes": "^FD data must open with <EC><input mode>, e.g. ^FDQA,<data> (p.129: Mandatory); model 1/2, EC H/Q/M/L, mask 0-7"},
    "aztec": {"cmd": "^BO", "page": 124, "data": "full ASCII", "notes": "1-33 rows"},
}

# ---------------------------------------------------------------------------
# Link-OS SGD commands (from the manual's examples + the Link-OS SGD reference
# at techdocs.zebra.com/link-os; verify names against the printer with SGD getvar)
# ---------------------------------------------------------------------------

SGD_COMMANDS: dict[str, dict] = {
    "! U1 setvar \"device.ip.address\" \"192.168.1.10\"\r\n^XA": {
        "name": "device.ip.address", "kind": "setvar", "page_link": "link-os/sgd.html",
        "notes": "Static IP; also set device.ip.address.mode static/dhcp/link-local"},
    "! U1 getvar \"$device.serial_number\"": {"name": "$device.serial_number", "kind": "getvar"},
    "! U1 getvar \"$net.interface.EtherNet:port.0.status\"": {"name": "net interface status", "kind": "getvar"},
    "! U1 setvar \"device.date\" \"10/22/26\"": {"name": "device.date", "kind": "setvar"},
    "! U1 setvar \"device.time\" \"12:00:00\"": {"name": "device.time", "kind": "setvar"},
    "! U1 getvar \"device.ip.address\"": {"name": "device.ip.address", "kind": "getvar"},
    "! U1 setvar \"wlan.ip.address.mode\" \"dhcp\"": {"name": "wlan.ip.address.mode", "kind": "setvar"},
    "! U1 setvar \"wlan.ssid\" \"MyNetwork\"": {"name": "wlan.ssid", "kind": "setvar"},
    "! U1 setvar \"wlan.wpa2.psk\" \"secret\"": {"name": "wlan.wpa2.psk", "kind": "setvar",
        "notes": "WPA2 passphrase — set locally at the printer UI when possible; avoid sending secrets"},
    "! U1 setvar \"ip.rtcmode\" \"ntp\"": {"name": "ip.rtcmode", "kind": "setvar"},
    "! U1 setvar \"ip.ntp_server1.address\" \"pool.ntp.org\"": {"name": "ip.ntp_server1.address", "kind": "setvar"},
    "! U1 setvar \"printer.print.darkness\" \"10\"": {"name": "printer.print.darkness", "kind": "setvar"},
    "! U1 setvar \"printer.print.speed\" \"4\"": {"name": "printer.print.speed", "kind": "setvar",
        "notes": "inches per second, model-dependent range"},
    "! U1 setvar \"printer.calibration.media\" \"calibrate\"": {"name": "printer.calibration.media", "kind": "setvar",
        "notes": "triggers a media calibration (also 'do:calibration')"},
    "! U1 setvar \"printer.language.code.page\" \"28\"": {"name": "printer.language.code.page", "kind": "setvar"},
    "! U1 setvar \"sensor.paper.type\" \"noncontinuous\"": {"name": "sensor.paper.type", "kind": "setvar",
        "notes": "continuous | noncontinuous | mark | black mark"},
    "! U1 setvar \"device.language\" \"ZPL\"": {"name": "device.language", "kind": "setvar"},
    "! U1 setvar \"device.backup.reset\" \"on\"": {"name": "device.backup.reset", "kind": "setvar"},
    "! U1 getvar \"$~\"": {"name": "getvar $~ (all variables)", "kind": "getvar",
        "notes": "dumps every SGD variable/value — the Link-OS way to read printer config"},
    "! U1 getvar \"printer.feed.dimension.label.length\"": {"name": "printer.feed.dimension.label.length", "kind": "getvar"},
    "! U1 getvar \"printer.width.print\"": {"name": "printer.width.print", "kind": "getvar"},
    "! U1 setvar \"device.snmp\" \"enable\"": {"name": "device.snmp", "kind": "setvar"},
    "! U1 setvar \"device.web_server\" \"enable\"": {"name": "device.web_server", "kind": "setvar"},
    "! U1 setvar \"printer.status.poll_interval\" \"5\"": {"name": "printer.status.poll_interval", "kind": "setvar"},
    "! U1 setvar \"printer.status.filter\" \"64570\"": {"name": "printer.status.filter", "kind": "setvar",
        "notes": "host:port for async status notifications (ZBI II status protocol)"},
    "! U1 getvar \"$printer.status.poll_interval\"": {"name": "poll interval read", "kind": "getvar"},
    "! U1 do:calibration": {"name": "do:calibration", "kind": "do",
        "notes": "media/sensor calibration action"},
}

# Status message codes (manual 'Status Commands' / ZBI II error hex words)
STATUS_NOTES: dict[str, dict] = {
    "0000": {"name": "PRINTER READY", "level": "ok"},
    "0001": {"name": "PRINTER NOT READY", "level": "warn"},
    "0002": {"name": "HEAD OPEN", "level": "error"},
    "0003": {"name": "PAPER OUT", "level": "error"},
    "0004": {"name": "REPRINT ERROR", "level": "error"},
    "0008": {"name": "HEAD COLD", "level": "warn"},
    "0010": {"name": "PAUSED", "level": "warn"},
    "0080": {"name": "OUT OF MEMORY", "level": "error"},
    "0200": {"name": "MEDIA OUT OF SPEC", "level": "warn"},
    "1000": {"name": "STOPPED DUE TO ERROR", "level": "error"},
    "2000": {"name": "RECALL ERROR", "level": "error"},
    "4000": {"name": "MEDIA JAM", "level": "error"},
    "8000": {"name": "TOO HOT", "level": "warn"},
}


def _barcode_command_notes() -> dict[str, dict]:
    """One entry per barcode command, derived from builder.BARCODE_SPECS.

    Every symbology generate_zpl can emit therefore also resolves through
    zpl_knowledge(mode='command', query='^B9'), which previously answered
    "not found" for ^B0/^B1/^B9/^BA/^BB/^BD/^BE/^BF/^BI/^BJ/^BK/^BM/^BO/^BP/^BR/^BS.
    """
    from .builder import BARCODE_SPECS  # local import: builder has no knowledge dep
    notes: dict[str, dict] = {}
    for sym, spec in BARCODE_SPECS.items():
        cmd = spec["cmd"]
        if cmd in COMMAND_NOTES or cmd in notes:
            continue
        page = spec["page"].lstrip("p.")
        fmt = cmd + ",".join(spec["letters"])
        notes[cmd] = {
            "page": int(page),
            "name": spec["name"],
            "syntax": fmt,
            "notes": f"Format line {fmt} (manual p.{page}). Symbology '{sym}' — "
                     f"see zpl_knowledge(mode='barcodes') for data rules.",
        }
    return notes


COMMAND_NOTES.update(_barcode_command_notes())


def get_command(name: str) -> dict | None:
    return COMMAND_NOTES.get(name)


def search(pattern: str, limit: int = 20) -> list[dict]:
    pat = re.compile(pattern, re.I)
    results = []
    for cmd, meta in COMMAND_NOTES.items():
        hay = f"{cmd} {meta.get('name','')} {meta.get('notes','')} {meta.get('syntax','')}"
        if pat.search(hay):
            results.append({"command": cmd, **meta})
            if len(results) >= limit:
                break
    return results


__all__ = ["known_commands", "COMMAND_NOTES", "BARCODE_NOTES", "SGD_COMMANDS",
           "STATUS_NOTES", "get_command", "search", "MANUAL_TXT"]
