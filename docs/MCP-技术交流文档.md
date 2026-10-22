# Zebra 打印 MCP 服务器 — 技术交流文档

**项目**：`zebra-printer-mcp` — ZPL 标签生成 + Zebra Link-OS 打印机控制的 stdio MCP 服务器
**位置**：`E:\workspace\zebra-printer-mcp` ｜ **版本**：0.1.0 ｜ **环境**：Windows / Python ≥3.10（实测 3.14）
**知识底座**：ZPL II Programming Guide（`zpl-zbi2-pg-en-202609.pdf`，全文抽取 44,030 行）+ Link-OS 文档（techdocs.zebra.com/link-os）
**实机验证**：Zebra ZR668-200dpi（局域网 IP，9100 端口，固件 V86.20.21Z）— 网络打印、状态查询、中文打印全部跑通

---

## 1. 这个项目解决什么问题

LLM/Agent 要驱动一台 Zebra 标签打印机， ordinarily 需要：懂 ZPL II 语法、知道每条命令的默认值和范围、
知道 Link-OS SGD 命令、处理字符编码、还得面对老固件的兼容性问题。这些知识琐碎且容易出错——
让模型凭记忆写 ZPL，常见的后果是：条码参数顺序错、中文变 `?`、相对量指令（`^MD`）跨任务累积、
对老固件发它不支持的 SGD。

本服务器把这些能力封装成 **6 个 MCP 工具**，让任何 MCP 客户端（Hermes、Claude Desktop、Codex 等）
通过 JSON-RPC 安全地生成、校验、发送标签并查询打印机：

| 工具 | 一句话 |
|---|---|
| `generate_zpl` | JSON 标签描述（mm 坐标）→ 合法 ZPL II，内置手册默认值与范围校验 |
| `validate_zpl` | 用从手册解析出的 **251 条命令目录** 静态校验任意 ZPL |
| `zpl_knowledge` | 查命令语法/默认值/页码、条码制式目录、SGD 命令、状态码表 |
| `list_printers` | 枚举 Windows 打印队列（定位 Zebra 驱动队列） |
| `send_zpl` | 发送 ZPL：`tcp`(9100 raw) / `windows`(假脱机) / `usb` / `preview`，或 `dry_run` 只校验 |
| `printer_status` | 探活 + 状态 + SGD 查询/设置（内置老固件 `~HI/~HS` 回退） |

设计原则：**每一条校验和默认值都有手册页码背书**（builder 的 issue 消息里直接带 `^PW p.329` 这类引用），
出问题可以回溯到手册原文核对。

---

## 2. 架构

```
┌─────────────────────────────────────────────────────────┐
│ MCP 客户端（Hermes / Claude Desktop / 任何 stdio 客户端）│
│        JSON-RPC 2.0 over stdin/stdout（换行分隔）        │
└──────────────────────────┬──────────────────────────────┘
                    ┌──────▼──────┐
                    │  server.py  │  FastMCP("zebra-printer")，6 个 @mcp.tool
                    └──┬───┬───┬──┘
          ┌────────────┘   │   └────────────┐
   ┌──────▼──────┐  ┌──────▼──────┐  ┌──────▼──────┐
   │ builder.py  │  │transport.py │  │knowledge.py │
   │ 580 行      │  │ 197 行      │  │ 294 行      │
   │ spec→ZPL    │  │ TCP/队列/   │  │ 命令/条码/  │
   │ 范围校验    │  │ 预览/编码    │  │ SGD/状态码  │
   └──────┬──────┘  └─────────────┘  └──────┬──────┘
          │        ┌────────────┐           │
          └────────│ catalog.py │───────────┘   13 行：把 knowledge 的
                   └────────────┘             251 命令目录注入 builder 校验器
```

**数据资产**（`data/`）：
- `zpl_manual_full.txt`（44,030 行）— 手册全文抽取，builder 默认值/范围从这里核对
- `zpl_command_secs.txt`（176 行）— 手册各命令章节索引，`known_commands()` 目录来源
- `zpl_params_notes.txt`（307 行）— 49 条命令的参数范围核对笔记

**模块依赖方向**：`knowledge → builder`（catalog 桥接，避免循环导入）、`server → 全部`。
`builder.py` 顶部的 `KNOWN_COMMANDS` 默认为空集合，`catalog.py` 在服务启动时填充——
单独 import builder 时校验退化为"不报 UNKNOWN_CMD"，不会崩。

---

## 3. MCP 协议层

- **形态**：stdio 服务器。`python -m zebra_mcp.server` → `FastMCP.run(transport="stdio")`，无 HTTP 端口。
- **框架**：FastMCP（`mcp.server.fastmcp`），依赖钉 **`mcp>=1.9,<2`**——2.x 重构后该导入路径消失，
  这是本项目最重要的版本坑（见 §11）。
- **协议版本协商**：客户端 `initialize` 携带 `protocolVersion`（实测 `2025-06-18` 正常），服务器回
  `serverInfo: {name: "zebra-printer", version: "0.1.0"}`。
- **返回值约定**：所有工具返回 JSON 字符串（`json.dumps(ensure_ascii=False, indent=2)`）放在
  `result.content[0].text`，客户端解析时要 **再 loads 一层**。

### 握手与调用示例（可直接贴给任何 stdio 客户端做联调）

```jsonc
// → initialize
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{
  "protocolVersion":"2025-06-18","capabilities":{},
  "clientInfo":{"name":"smoke","version":"1.0"}}}
// ← 收到响应后再发
{"jsonrpc":"2.0","method":"notifications/initialized"}
// → tools/list  → 返回 6 个工具的完整 inputSchema
// → tools/call
{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{
  "name":"generate_zpl","arguments":{"label_spec":{"dpi":200,"width_mm":60,"height_mm":40,
  "fields":[{"type":"barcode","symbology":"code128","data":"ABC12345"}]}}}}
```

**探针纪律**（实测踩出来的，写自动化测试必用）：
- **一次一请求**：写一行 JSON、flush、从 stdout 恰好读一行响应；批量 printf 全部请求再关闭
  stdin 会让服务器在握手完成前退出，后面的响应静默丢失（表现：只收到前几条）。
- 参数名从 `server.py` handler 签名 grep 得来——FastMCP 把 Python 关键字参数原样暴露，
  猜名字得到的是 `isError` 或空 content。正确参数名：`label_spec` / `zpl` / `query` / `host`。
- 服务器日志走 stderr，与 stdout 协议流量分开收集。

---

## 4. 六个工具详解

### 4.1 `generate_zpl(label_spec) -> {zpl, issues[]}`

输入 JSON 标签描述，输出 ZPL + 校验问题列表（error/warning，均带手册页码）。

**label spec 顶层键**（builder docstring 与实现核对过）：

| 键 | 映射 ZPL | 备注 |
|---|---|---|
| `dpi` | `^PW`/`^LL` 换算基准 | 152/200/203/300/600；其他值 warning |
| `width_mm`/`height_mm` | `^PW`/`^LL` | mm→dot：`round(mm×dpi/25.4)` |
| `quantity` | `^PQ` | 1–99999999 |
| `darkness` | **`~SD`（绝对值）** | ⚠️ 见 §11 踩坑 3 |
| `char_encoding` | `^CI` | 含中文时自动 `^CI28`（见 §6） |
| `print_speed`/`slew_speed`/`backfeed_speed` | `^PR` | ips |
| `mirror` / `invert` | `^PM` / `^PO` | |
| `shift_left` / `label_top` | `^LT` | dot |
| `media_tracking` `{mode,black_mark_offset}` | `^MN` | mode ∈ N/Y/W/M/B/A（p.307） |
| `print_mode` `{mode,media_loss}` | `^MM` | mode ∈ T/P/R/A/K（p.305） |
| `barcode_defaults` | `^BY` | 手册默认 width=2, ratio=3, height=10（p.239） |
| `default_font` / `font_file` | `^A6` / `^A@` | font_file 见 §6 |
| `fields[]` | 见下 | 顺序即发射顺序 |

**字段类型**（`type`）：`text`（默认）、`barcode`、`qrcode`（独立类型，也支持 `type:"barcode"`+`symbology:"qrcode"`）、
`note`（`^FB` 多行块）、`box`/`circle`（`^GB`）、`image`（`^GBD`）、`reverse`（`^GR` 反白包裹后续字段）。

**坐标**：`x_mm/y_mm` 或 `x_dots/y_dots`（barcode/text 的 `height_pt`、`height_mm`、`module_width` 均可）。

**支持 25 种条码制式**（`BARCODE_CMDS`，手册 66–150 页逐条映射）：
code128(`^BC` p.94)、code39(`^B3`)、code93、i2of5、pdf417(`^B7`)、micropdf417、datamatrix(`^BX`)、
qrcode(`^BQ` p.128)、aztec、ean13/ean8/upca/upce、gs1databar、codabar、msi、plessey、code11、code49、
codablock、planet、postnet/usps_im(`^BZ`)、upsmaxicode(`^BD`)、std2of5、logmars、upcext。
每种制式的数据规则（i2of5 需偶数位数字、upca 11 位+校验、maxicode 62 字符集等）在 builder 和
`zpl_knowledge mode=symbologies` 中都有。

**ZPL 发射规则**（全部经手册核对，也是与"凭记忆写 ZPL"的模型对话时最常纠正的点）：
- 条码命令按 **Format 行字母顺序**逗号分隔发射，尾部缺省省略、中段缺省留空位——合法形如 `^BCN,,Y`。
- 字段坐标 `^FO` 必须先于条码/数据命令发射（p.201）——否则多条码全部叠在原点。
- hex 转义生效时在 `^FD` 前发 `^FH`。
- 二维码 `^BQ` 走 scale/model/纠错参数（p.128）。

### 4.2 `validate_zpl(zpl) -> {issues[], command_count}`

静态校验三层：
1. 提取 payload 中全部 `^XY`/`~XY` 双字符 token，对照 **251 条手册命令目录**，未命中 → `UNKNOWN_CMD` warning；
2. `^XA`/`^XZ` 配对检查（不配对 → error，p.370/375）；
3. 完全无 ZPL 命令 → warning。

干净 ZPL 零误报（`^ZZ` 这类冷命令也在目录内——目录来自手册全文抽取，不是手写白名单）。

### 4.3 `zpl_knowledge(query, mode)`

| mode | 查什么 | 例 |
|---|---|---|
| `command`（默认） | `^`命令语法/默认值/页码 | `query="barcode"` 搜名称+notes |
| `barcode` | 条码制式数据规则 | `query="maxicode"` |
| `sgd` | Link-OS SGD 命令目录 | `query="calibration"` |
| `status` | `~HS` 状态码表 | `0000=READY, 0002=HEAD OPEN, 0003=PAPER OUT, 8000=TOO HOT…` |
| `symbologies` | 25 种制式总表 | 空 query |

### 4.4 `list_printers()`

PowerShell `Get-Printer` 枚举（Name/PortName/DriverName/PrinterStatus/Shared）。
用 PowerShell 而非 ctypes winspool 的原因：win32print 在部分 Python 构建里直接段错误（实测 exit 139）。

### 4.5 `send_zpl(zpl, transport_kind, dry_run, host, port, printer, preview_name)`

| transport | 通道 | 关键点 |
|---|---|---|
| `tcp` | `host:9100` raw | Zebra 标准 raw 口；发送后短暂收 echo（9100 通常不回显，有 echo 即错误文本） |
| `windows` | PowerShell `Print-File` 假脱机 | 仅当队列驱动是 Zebra ZPL 驱动才保真；否则返回 `zebra_zpl_driver_detected:false` + hint |
| `usb` | 实为 Windows 队列（USB 打印必过队列） | 找不到队列时返回可用队列列表 |
| `preview` | 写 `%TEMP%\zebra-mcp\label_preview.zpl` | 可贴 labelary.com 渲染核对 |
| `dry_run=true` | 只 validate + ASCII 预览 | 不发送 |

返回值统一 `{ok, transport, bytes_sent, response, hint}`，失败带诊断 hint。

### 4.6 `printer_status(host, port, sgd)`

- 不传 `sgd` → 先 SGD `! U1 getvar "$device.serial_number"`；**失败自动回退原生状态命令**：
  `~HI`（打印机识别：型号/固件/dot 密度/内存）+ `~HS`（状态：接口类别、缺纸/暂停/缓冲/温度各位、
  标签长度、状态码 `0000 = PRINTER READY`）。回退结果带 `sgd_error` 说明为什么回退。
- 传 `sgd` → 原样下发 `! U1 …`（getvar/setvar/do），`SGD_COMMANDS` 目录是查询起点。

---

## 5. 实机数据：ZR668-200dpi 全链路记录

| 项 | 实测值 |
|---|---|
| 识别（`~HI`） | `ZR668-200dpi, V86.20.21Z, 8 dots/mm, 8192KB` |
| SGD 兼容性 | **getvar 返回 `?`** —— V86.20.21Z 属 Fusion 系 pre-Link-OS，无完整 SGD |
| 状态（`~HS`） | 接口 159（网络）；缺纸/暂停/缓冲满/温度全 0；标签长 315 dots ≈ 39.4 mm；`0000 = PRINTER READY` |
| TCP 打印 | 多张 Code128+QR+文本标签，`send_tcp` `ok=True, 105 bytes`，零错误回显 |
| MCP 端到端 | `generate_zpl` → `send_zpl(tcp)` 中文标签 `ok=True, 234 bytes` |
| 固件目录 | web 后台 `/dir?dev=E:&otype=.TTF` 列出：`E:SIMSUN.TTF`、`E:HANS.TTF`、`E:TT0003M_.TTF` |

---

## 6. 中文打印（本次最重要的实测结论）

**ZR668 内置中文 TrueType 字体**，无需下载：`E:SIMSUN.TTF`（宋体，10.5MB）、`E:HANS.TTF`（思源黑，19MB）。
（此前"ZR668 无内置 CJK 字体、需 `~DY` 下载 TTF"的结论是错的，已被打印机 web 目录 + 实机打印推翻。）

**中文标签配方三件套**：

1. **编码**：`^CI28`（UTF-8）。ZR668 的 `E:` 没有 `.DAT` 国际编码表，GB2312/GBK 单字节 codepage 路线不适用；
   `generate_zpl` 检测到字段含 CJK 自动发 `^CI28` 并 warning 提示配字体。
2. **字节流**：transport 对 `^CI28/^CI29/^CI30` + 非 ASCII 的 payload **自动按 UTF-8 编码上线**
   （历史 bug：统一 cp1252 `errors="replace"` 把所有中文在上线前替换成 `?`——这是"中文打不出来"的真正根因）。
3. **字体**：字段加 `"font_file": "E:SIMSUN.TTF"` → 生成 `^A@N,高,,E:SIMSUN.TTF`
   （`^A@ o,h,w,d:f.x`，p.62；省略宽度走默认）。同名字段后续 `^A@` 可省名复用。

已实测通过的 spec 样例：

```json
{"dpi": 200, "width_mm": 70, "height_mm": 40, "darkness": 10,
 "fields": [
   {"type": "text", "text": "条码AI一线 宋体", "x_mm": 3, "y_mm": 2,
    "height_pt": 11, "font_file": "E:SIMSUN.TTF"},
   {"type": "text", "text": "条码AI一线 思源黑", "x_mm": 3, "y_mm": 8,
    "height_pt": 11, "font_file": "E:HANS.TTF"},
   {"type": "text", "text": "中文测试 0123456789", "x_mm": 3, "y_mm": 14, "height_pt": 11},
   {"type": "barcode", "symbology": "code128", "data": "MCP-CJK-20261022",
    "x_mm": 3, "y_mm": 21, "height_mm": 12}
 ]}
```

→ `^XA^PW552^LL315^CI28 ... ^FO12,16^A@N,28,,E:SIMSUN.TTF^FD条码AI一线 宋体^FS ... ^XZ`
→ `send_tcp` 234 bytes，出纸正常。

---

## 7. 客户端注册

### Hermes

```bat
hermes config set mcp_servers.zebra-printer "{\"command\":\"E:/workspace/zebra-printer-mcp/.venv/Scripts/python.exe\",\"args\":[\"-m\",\"zebra_mcp.server\"],\"cwd\":\"E:/workspace/zebra-printer-mcp\",\"connect_timeout\":30.0,\"enabled\":true}"
hermes mcp test zebra-printer     :: ✓ Connected (~1.5s)，6 tools discovered
```

⚠️ 两个 Hermes 侧坑：JSON 里 Windows 路径必须用**正斜杠**（反斜杠会被 JSON/YAML 双层转义吃掉）；
**不要直接 patch `config.yaml`**（配置保护会拒），走 `hermes config set`。

### Claude Desktop / 通用 stdio 客户端

```json
{"mcpServers": {"zebra-printer": {
  "command": "E:/workspace/zebra-printer-mcp/.venv/Scripts/python.exe",
  "args": ["-m", "zebra_mcp.server"],
  "cwd": "E:/workspace/zebra-printer-mcp"}}}
```

### 手工跑

```bat
cd /d E:\workspace\zebra-printer-mcp
uv venv .venv --python 3.14
uv pip install --python .venv\Scripts\python.exe -e .
.venv\Scripts\python.exe -m zebra_mcp.server
```

---

## 8. 典型交互序列（给客户端 Agent 的操作手册）

1. **探活**：`printer_status(host="<打印机IP>")` → 看 `identity`/`status_code=0000`。
2. **生成**：`generate_zpl(label_spec)` → 检查 `issues`，error 必须修，warning 酌情。
3. **自检**：`validate_zpl(zpl)`（模型手写 ZPL 时尤其要过这关）。
4. **试跑**：`send_zpl(zpl, dry_run=true)` 看 ASCII 预览布局。
5. **实发**：`send_zpl(zpl, transport_kind="tcp", host=...)`；Windows 队列打印机先 `list_printers`。
6. **调参**：`printer_status(sgd="! U1 getvar \"printer.print.darkness\"")`（仅限支持 SGD 的机型）。
7. **事后复位**：改过 darkness 后用 `printer_status(sgd="! U1 setvar \"printer.print.darkness\" \"10\"")` 或
   发一张 `~SD10` 标签复位（见踩坑 3）。

---

## 9. 测试与验证方法

| 层 | 手段 | 状态 |
|---|---|---|
| 单元 | `smoke_test2.py`：builder 默认值/范围/条码制式/validate 正反例（`^QQ` 报 UNKNOWN、`^ZZ0` 不报） | `ALL TESTS PASSED` |
| 协议 | stdio 探针（initialize→initialized→tools/list→tools/call 逐条驱动） | 6 工具全通 |
| 实机 | TCP 9100 打印、`~HI/~HS`、web 目录、中文字体对比标签 | 全部通过（ZR668） |
| 编译 | `py_compile` 全模块 | 通过 |

---

## 10. 已知边界与路线图

- **老固件**（Fusion 系）：SGD 基本不可用，依赖原生 `~` 命令回退；更老的 Xi4 系部分命令不支持。
- **Windows USB raw**：Windows 无法绕过驱动直发 raw ZPL；USB 保真路径 = Zebra 官方驱动队列，或换网络口。
- **渲染预览**：ASCII 预览是近似布局（dot→字符格换算 ÷6/÷12），精确预览请用 labelary 或实机。
- **未做**：ZPL 二进制格式（`^D`/`~DY` 字体下载通道已有知识、未封装成工具）；`~DY` 下载工具值得下一步补上
  （对没有内置 CJK 的机型有用）。
- **扩展方向**：`^DF` 格式调用、`^HC` 目录读取、labelary 渲染回传、SGD 变量全量 dump 解析。

---

## 11. 踩坑清单（全部为实测付出代价换来）

1. **`mcp` 2.x 没有 `mcp.server.fastmcp`** → 依赖必须钉 `mcp>=1.9,<2`（pyproject 已钉）。
2. **Hermes 配置 JSON 用正斜杠路径**；直接 patch `config.yaml` 被配置保护拒绝。
3. **`^MD` 是相对增量**（p.301），跨任务累积会把 darkness 顶到上限；绝对值用 `~SD`（p.333）。builder 已改用 `~SD`。
4. **cp1252 统一编码把中文变 `?`** → `^CI28` + 非 ASCII 自动 UTF-8 上线（transport 已修）。
5. **条码参数必须按 Format 字母序逗号发射**，中段缺省留空位（`^BCN,,Y` 合法）；乱序/省略逗号是模型手写 ZPL 的头号错误。
6. **`^FO` 必须先于条码命令**，否则多条码叠在原点。
7. **`^BY` 手册默认 ratio=3**（不是凭印象的 2）——默认值必须查手册而不是猜。
8. **老固件 SGD 返回 `?`** ≠ 连接失败，是"不支持"；`printer_status` 自动走 `~HI/~HS`。
9. **`ctypes` winspool 枚举打印机会段错误**（exit 139）→ PowerShell `Get-Printer`。
10. **批量 stdin 管道驱动 stdio MCP 会丢响应** → 一次一请求（§3 探针纪律）。
11. **冒烟测试挑"非法命令"要用真不在目录里的**（`^QQ`）——`^ZZ` 其实是手册里的诊断命令，用它做反例会误判校验器。

---

## 12. 源码导览

| 文件 | 行数 | 核心内容 |
|---|---|---|
| `src/zebra_mcp/server.py` | 271 | FastMCP 入口、6 工具、SGD↔原生状态回退逻辑 |
| `src/zebra_mcp/builder.py` | 580 | `build_label`（spec→ZPL）、`validate_zpl`、25 制式 `BARCODE_CMDS`/`BARCODE_SPECS`、`^A@` font_file、默认值/范围校验（带页码） |
| `src/zebra_mcp/transport.py` | 197 | `_clean_zpl` 编码仲裁、`send_tcp`、`list_windows_printers`、`send_windows_printer`、`ascii_preview`、`write_preview` |
| `src/zebra_mcp/knowledge.py` | 294 | `COMMAND_NOTES`、`BARCODE_NOTES`、`SGD_COMMANDS`、`STATUS_NOTES`、`known_commands()` |
| `src/zebra_mcp/catalog.py` | 13 | 把 251 命令目录注入 builder 校验器 |
| `data/zpl_manual_full.txt` | 44,030 | ZPL II 手册全文（一切默认值的出处） |

---

*文档生成于 2026-10-22；所有实测数据来自一台 ZR668-200dpi 实机（局域网 9100）与 MCP stdio 探针。*
