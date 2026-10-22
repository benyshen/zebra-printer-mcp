# zebra-printer-mcp

ZPL 标签生成 + Zebra Link-OS 打印机控制的 **stdio MCP 服务器**（Windows 实机跑通，Linux/macOS 除 `windows`/`usb` 传输外通用）。

> **技术交流完整版文档**：[`docs/MCP-技术交流文档.md`](docs/MCP-技术交流文档.md)
> （架构图、6 工具详解、label spec 全键表、ZR668 实机数据、中文打印配方、stdio 探针纪律、11 条踩坑清单）

知识底座：ZPL II Programming Guide（Zebra 官方 PDF，本机全文抽取）+ Link-OS 文档 <https://techdocs.zebra.com/link-os/latest/> 。实机验证：Zebra ZR668-200dpi（固件 V86.20.21Z）— 网络打印、状态查询、中文打印全部跑通。

## 快速开始

```bash
git clone https://github.com/benyshen/zebra-printer-mcp.git
cd zebra-printer-mcp
uv venv .venv --python 3.12      # 或 python -m venv .venv
# Windows:
.venv\Scripts\pip install -e .
# Linux/macOS:
.venv/bin/pip install -e .

# 自检（不需要打印机）
.venv/bin/python tests/test_builder.py    # → ALL TESTS PASSED
.venv/bin/python scripts/mcp_probe.py     # raw JSON-RPC 探针，列出 6 工具
```

> **必须 `mcp<2`**：2.x 移除了 `mcp.server.fastmcp`，pyproject 已钉 `mcp>=1.9,<2`。

## 6 个 MCP 工具

| 工具 | 作用 |
|---|---|
| `generate_zpl` | JSON label spec → ZPL II（mm 坐标自动换算 dot；内置手册默认值/范围校验，返回 issues） |
| `validate_zpl` | 用手册解析出的命令目录校验任意 ZPL |
| `zpl_knowledge` | 查命令语法/默认值/页码、SGD 命令、`~HS` 状态码、条码制式目录 |
| `list_printers` | 枚举 Windows 打印队列（找 Zebra 队列） |
| `send_zpl` | 发送：`tcp`(9100 raw) / `windows`(假脱机) / `usb` / `preview`；`dry_run` 只校验+预览 |
| `printer_status` | 查询打印机状态：Link-OS SGD 优先，老固件自动回退原生 `~HI`/`~HS` |

## label spec 示例（generate_zpl 入参）

```json
{
  "dpi": 200, "width_mm": 70, "height_mm": 30, "quantity": 2, "darkness": 10,
  "fields": [
    {"type": "text", "data": "条码AI一线", "x_mm": 2, "y_mm": 2, "size_mm": 3.5,
     "font_file": "E:SIMSUN.TTF"},
    {"type": "barcode", "symbology": "code128", "data": "ABC12345",
     "x_mm": 2, "y_mm": 9, "height_mm": 12, "interpret": true},
    {"type": "barcode", "symbology": "qrcode", "data": "https://example.com",
     "x_mm": 45, "y_mm": 9, "scale": 4},
    {"type": "box", "x_mm": 1, "y_mm": 1, "width_mm": 68, "height_mm": 28}
  ]
}
```

坐标默认 mm（`x_mm/y_mm`），也可给 `x_dots/y_dots`。条码制式 25 种：code128/code39/code93/
i2of5/upca/upce/ean13/pdf417/datamatrix/qrcode/aztec/codabar/msi/code11…（见
`zpl_knowledge mode=symbologies`）。

## 中文打印（ZR668 实机验证）

ZR668-200dpi 内置中文 TrueType：`E:SIMSUN.TTF`（宋体）、`E:HANS.TTF`（思源黑）——打印机 web 后台 `/dir?dev=E:&otype=.TTF` 可列目录。配方：

- 中文字段加 `"font_file": "E:SIMSUN.TTF"` → 生成 `^A@N,高,,E:SIMSUN.TTF`；含中文自动 `^CI28`
- transport 对 `^CI28` + 非 ASCII 自动按 **UTF-8 字节**发送（cp1252 会把中文变 `?`）
- 已实测：宋体/思源黑两行 `^CI28` UTF-8 中文 + Code128 经 MCP `generate_zpl` → `send_zpl(tcp)` 打印成功

## MCP 客户端注册

### Hermes

```bash
hermes config set mcp_servers.zebra-printer '{"command":"<repo路径>/.venv/Scripts/python.exe","args":["-m","zebra_mcp.server"],"cwd":"<repo路径>","connect_timeout":30.0,"enabled":true}'
hermes mcp test zebra-printer   # ✓ Connected, 6 tools discovered
```

（JSON 里用**正斜杠**路径——`\\w` 这类反斜杠会被转义吃掉。）

### Claude Desktop / 其它 stdio 客户端

```json
{
  "mcpServers": {
    "zebra-printer": {
      "command": "<repo路径>/.venv/Scripts/python.exe",
      "args": ["-m", "zebra_mcp.server"],
      "cwd": "<repo路径>"
    }
  }
}
```

Linux/macOS 把 `command` 换成 `.venv/bin/python`。

## 接上物理 Zebra 打印机

1. 网络打印机：`printer_status host=<IP>` 先探活 → `send_zpl transport_kind=tcp host=<IP>`。
2. USB/驱动打印机：`list_printers` 找队列名 → `send_zpl transport_kind=windows printer=<队列名>`。
   注意：Windows 假脱机走驱动，装了 Zebra ZPL 驱动才保真；否则用 tcp raw。
3. SGD 调参：`printer_status sgd=device.ip.address`、`sgd=do:calibration` 等；老 Fusion 固件
   （如 ZR668 V86）SGD 不可用，工具自动回退 `~HI`/`~HS`。
4. 无打印机时可先用 `transport_kind=preview`（写 `%TEMP%\zebra-mcp\label_preview.zpl`，
   可在 <https://www.labelary.com> 渲染核对）。

## data/ 恢复表（不入库的文件）

仓库提交 `data/command_catalog.txt`（命令目录，运行时必需）。以下是从 Zebra 版权手册 PDF
派生的大文件，**不提交**；需要时用自己的 PDF 副本重建：

| 文件 | 作用 | 恢复方法 |
|---|---|---|
| `data/zpl_manual_full.txt` | 手册全文（页锚 `<<<PAGE n>>>`） | `pip install pypdf` → `python scripts/extract_manual.py <你的 zpl-zbi2-pg-en PDF>` |
| `data/zpl_command_secs.txt` | 手册命令章节索引 | 同上脚本一并生成 |
| `data/zpl_params_notes.txt` | 命令参数/Format-line 笔记 | 同上脚本一并生成 |

PDF 来源：Zebra 官网 ZPL II Programming Guide（<https://www.zebra.com/zpl>）。

## License

MIT
