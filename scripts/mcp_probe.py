"""Minimal raw JSON-RPC probe against the stdio MCP server — speaks the MCP
protocol with zero dependencies, useful for debugging any MCP client wiring.

Run: python scripts/mcp_probe.py
Discipline: one request, wait for its response, then the next. Batching several
requests into one stdin pipe makes the server answer out of the moment you are
reading (tools/list disappears).
"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable

p = subprocess.Popen(
    [PY, "-m", "zebra_mcp.server"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    cwd=str(ROOT), env=dict(os.environ), text=True, encoding="utf-8")


def send(obj):
    p.stdin.write(json.dumps(obj) + "\n")
    p.stdin.flush()


send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
    "protocolVersion": "2025-06-18", "capabilities": {},
    "clientInfo": {"name": "probe", "version": "1.0"}}})
print("INIT:", json.loads(p.stdout.readline())["result"]["serverInfo"])
send({"jsonrpc": "2.0", "method": "notifications/initialized"})

send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
tools = json.loads(p.stdout.readline())["result"]["tools"]
print("TOOLS:", [t["name"] for t in tools])

send({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
    "name": "generate_zpl", "arguments": {"label_spec": {
        "dpi": 200, "width_mm": 70, "height_mm": 30,
        "fields": [{"type": "text", "data": "条码AI一线 probe", "x_mm": 2, "y_mm": 2,
                    "size_mm": 3.5, "font_file": "E:SIMSUN.TTF"}]}}}})
d = json.loads(p.stdout.readline())
print("GENERATE:", d["result"]["content"][0]["text"][:200])

send({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {
    "name": "validate_zpl", "arguments": {"zpl": "^XA^FO10,10^A0N,30^FDhello^FS^XZ"}}})
d = json.loads(p.stdout.readline())
print("VALIDATE issues:", json.loads(d["result"]["content"][0]["text"])["issues"])

send({"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {
    "name": "zpl_knowledge", "arguments": {"mode": "sgd", "query": "calibration"}}})
print("SGD:", json.loads(p.stdout.readline())["result"]["content"][0]["text"][:300])

p.stdin.close()
try:
    p.wait(timeout=5)
except subprocess.TimeoutExpired:
    p.kill()
print("DONE")
