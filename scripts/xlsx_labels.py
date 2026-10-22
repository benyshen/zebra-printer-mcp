#!/usr/bin/env python
"""Print one label per row of an .xlsx -- the CLI twin of the print_xlsx tool.

    python scripts/xlsx_labels.py 人员表.xlsx --name 姓名 --id 员工号 --dry-run
    python scripts/xlsx_labels.py 人员表.xlsx --name A --id B --host <打印机IP>

All labels are emitted as one ZPL stream and sent over a single TCP connection,
so the per-label echo wait is paid once for the whole batch.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from zebra_mcp import transport, xlsx_batch  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="xlsx rows -> centred name + Code128 labels")
    ap.add_argument("xlsx", help="path to .xlsx")
    ap.add_argument("--name", default="A", help="name column: letter, 1-based index, or header")
    ap.add_argument("--id", default="B", dest="id_col", help="barcode column (Code 128)")
    ap.add_argument("--sheet", default="", help="sheet name (default: first)")
    ap.add_argument("--width", type=float, default=50.0, help="label width mm (default 50)")
    ap.add_argument("--height", type=float, default=30.0, help="label height mm (default 30)")
    ap.add_argument("--name-size", type=float, default=4.0, help="name height mm (default 4)")
    ap.add_argument("--barcode-height", type=float, default=10.0, help="bars height mm (default 10)")
    ap.add_argument("--module-width", type=int, default=3, help="Code128 module width in dots")
    ap.add_argument("--no-header", action="store_true", help="first row is data, not a header")
    ap.add_argument("--out", default="", help="write the job to this .zpl file")
    ap.add_argument("--host", default="", help="printer IP; omit for dry run")
    ap.add_argument("--port", type=int, default=9100)
    ap.add_argument("--execute", action="store_true", help="really send (default: dry run)")
    args = ap.parse_args()

    rows = xlsx_batch.read_xlsx_rows(args.xlsx, args.sheet or None)
    specs = xlsx_batch.plan_specs(
        rows, args.name, args.id_col, width_mm=args.width, height_mm=args.height,
        name_size_mm=args.name_size, barcode_height_mm=args.barcode_height,
        module_width=args.module_width, skip_header=not args.no_header)
    job, issues = xlsx_batch.build_job(specs)

    out = Path(args.out) if args.out else (Path(args.xlsx).with_suffix(".zpl"))
    xlsx_batch.write_job(job, out)
    print(json.dumps({"rows": len(rows), "labels": len(specs),
                      "issues": [i["code"] for i in issues],
                      "bytes": len(job.encode("utf-8")), "zpl_file": str(out)},
                     ensure_ascii=False, indent=1))
    print("first label:", job.split("^XZ")[0] + "^XZ")

    if args.execute and args.host:
        res = transport.send_tcp(args.host, job, args.port)
        print("sent:", json.dumps(res, ensure_ascii=False))
    else:
        print("dry run — add --execute --host <IP> to print")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
