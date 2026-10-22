"""Rebuild the manual-derived data files from the ZPL II Programming Guide PDF.

The repo does NOT commit the full extracted manual text (copyrighted Zebra
publication). Keep the PDF out of git too. This script recreates, from your own
copy of zpl-zbi2-pg-en-202609.pdf (https://www.zebra.com/zpl — ZPL II Programming
Guide), everything the package needs for offline use:

    data/zpl_manual_full.txt    full text, page anchors "<<<PAGE n>>>"
    data/zpl_command_secs.txt   per-command sections pulled from the manual
    data/zpl_params_notes.txt   parameter/format-line notes per command
    data/command_catalog.txt    the ^/~ command catalog used by validate_zpl
                                (this one small file IS committed)

Usage:
    python scripts/extract_manual.py /path/to/zpl-zbi2-pg-en-202609.pdf

Only data/command_catalog.txt is required at runtime (known_commands() prefers
it); the other three are research aids used while developing the knowledge base.
"""
import re
import sys
from pathlib import Path

from pypdf import PdfReader

DATA = Path(__file__).resolve().parents[1] / "data"
_CMD_RE = re.compile(r"[~^][A-Z0-9@]{1,2}")
_SEC_RE = re.compile(r"<<<PAGE (\d+)>>>\s*ZPL Commands\s+(\^[A-Z0-9]{1,2})\s")


def extract_text(pdf: Path) -> str:
    reader = PdfReader(str(pdf))
    parts = []
    for i, page in enumerate(reader.pages, start=1):
        parts.append(f"<<<PAGE {i}>>>\n")
        try:
            parts.append(page.extract_text() or "")
        except Exception as exc:  # a broken page must not kill the whole run
            parts.append(f"[extract error: {exc}]")
        parts.append("\n")
    return "\n".join(parts)


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    pdf = Path(sys.argv[1])
    if not pdf.is_file():
        sys.exit(f"PDF not found: {pdf}")

    DATA.mkdir(exist_ok=True)
    text = extract_text(pdf)
    (DATA / "zpl_manual_full.txt").write_text(text, encoding="utf-8")
    print(f"zpl_manual_full.txt: {len(text.splitlines())} lines")

    (DATA / "command_catalog.txt").write_text(
        "\n".join(sorted(set(_CMD_RE.findall(text)))) + "\n", encoding="utf-8")
    print("command_catalog.txt:", len(set(_CMD_RE.findall(text))), "commands")

    # per-command sections (research aid)
    secs = []
    marks = list(_SEC_RE.finditer(text))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        secs.append(f"### PAGE {m.group(1)} CMD {m.group(2)}\n{text[m.start():end]}")
    (DATA / "zpl_command_secs.txt").write_text("\n".join(secs), encoding="utf-8")
    print("zpl_command_secs.txt:", len(secs), "sections")

    # parameter/format-line notes (research aid)
    notes = []
    for m in re.finditer(r"(Format:\s.*(?:\n(?!\n).*)*)", text):
        notes.append(m.group(1).strip())
    (DATA / "zpl_params_notes.txt").write_text("\n\n---\n\n".join(notes), encoding="utf-8")
    print("zpl_params_notes.txt:", len(notes), "notes")


if __name__ == "__main__":
    main()
