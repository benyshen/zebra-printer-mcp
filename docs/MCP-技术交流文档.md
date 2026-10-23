# Zebra 打印 MCP 服务器 — 技术交流文档

**项目**：`zebra-printer-mcp` — ZPL 标签生成 + Zebra Link-OS 打印机控制的 stdio MCP 服务器
**配套**：[`MCP-AI调用指南.md`](MCP-AI调用指南.md) —— 面向"该不该用、怎么交给 AI 用"的选型文档（优势 / 使用方法 / 适宜与不适宜场景 / 功能模块）。

**位置**：<https://github.com/benyshen/zebra-printer-mcp>（本地 `E:\workspace\zebra-printer-mcp`） ｜ **版本**：0.1.1 ｜ **环境**：Windows / Python ≥3.10（实测 3.14）
**知识底座**：ZPL II Programming Guide（`zpl-zbi2-pg-en-202609.pdf`，全文抽取 44,030 行）+ Link-OS 文档（techdocs.zebra.com/link-os）
**实机验证**：Zebra ZR668-200dpi（局域网 IP，9100 端口，固件 V86.20.21Z）— 网络打印、状态查询、中文打印全部跑通

---

## 1. 这个项目解决什么问题

LLM/Agent 要驱动一台 Zebra 标签打印机， ordinarily 需要：懂 ZPL II 语法、知道每条命令的默认值和范围、
知道 Link-OS SGD 命令、处理字符编码、还得面对老固件的兼容性问题。这些知识琐碎且容易出错——
让模型凭记忆写 ZPL，常见的后果是：条码参数顺序错、中文变 `?`、相对量指令（`^MD`）跨任务累积、
对老固件发它不支持的 SGD。

本服务器把这些能力封装成 **7 个 MCP 工具**，让任何 MCP 客户端（Hermes、Claude Desktop、Codex 等）
通过 JSON-RPC 安全地生成、校验、发送标签并查询打印机：

| 工具 | 一句话 |
|---|---|
| `generate_zpl` | JSON 标签描述（mm 坐标）→ 合法 ZPL II，内置手册默认值与范围校验 |
| `validate_zpl` | 用从手册解析出的 **253 条命令目录** 静态校验任意 ZPL（含 QR 前缀检查） |
| `zpl_knowledge` | 查命令语法/默认值/页码、条码制式目录、SGD 命令、状态码表 |
| `list_printers` | 枚举 Windows 打印队列（定位 Zebra 驱动队列） |
| `send_zpl` | 发送 ZPL：`tcp`(9100 raw) / `windows`(假脱机) / `usb` / `preview`，或 `dry_run` 只校验 |
| `printer_status` | 探活 + 状态 + SGD 查询/设置（内置老固件 `~HI/~HS` 回退） |
| `print_xlsx` | 读 `.xlsx` 两列（文本 + Code128），一行一张标签、各自居中，整批一个 ZPL 流 |
| `zpl_from_image` | 一张现有标签的照片 → 解码 + OCR + 像素→dot → ZPL（可 dry_run 或直发） |

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
  `serverInfo: {name: "zebra-printer", version: "1.30.0"}` —— **这里的 version 是 MCP SDK 的版本**，
不是本包的版本（FastMCP 在 SDK 1.x 不接受 `version=` 参数，直接把 SDK 版本透出）。本包版本见
`zebra_mcp.__version__`（当前 0.1.1）。
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
// → tools/list  → 返回 9 个工具的完整 inputSchema
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

## 4. 九个工具详解

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
| `default_font` | `^CF` | 全局默认字体 |
| `font_file`（字段级） | `^A@` | 打印机内置/下载的 TrueType，见 §6 |
| `fields[]` | 见下 | 顺序即发射顺序 |

**字段类型**（`type`，对应 `builder.build_label` 的分派）：

| type | 实际发射的 ZPL | 关键参数 |
|---|---|---|
| `text`（默认） | `^FO`/`^FT` → `^A0N` 或 `^A@` → (`^FB`) → (`^FH`) → `^FD` → `^FS` | `data`、`font` 或 `font_file`、`size_mm`、`block{}` |
| `barcode` | `^FO` → `^B*` → (`^FH`) → `^FD` → `^FS` | `symbology`、`data`、`height_mm`、各制式专属项（见下） |
| `note` / `box` | `^GB`（两者是同一命令，渲染函数已合并） | `thickness`、`color`(B/W)、`corner_rounding` |
| `circle` | `^GC` | `diameter_mm`、`thickness` |
| `reverse` | `^FR`（作用于后续字段） | — |
| `image` | `^XGR:<name>,1,1`（图需先 `~DG` 下载到打印机） | `name` |

> `^FB` **不是**独立字段类型，而是 `text` 的子键 `block:{width_mm, lines, line_height, alignment}`。

**坐标**：`x_mm/y_mm` 或 `x_dots/y_dots`（给 dots 就跳过换算）；尺寸同理用 `*_mm` / `*_dots`。
条码高度用字段级 `height_mm`/`height_dots` 覆盖，全局默认走 `barcode_defaults`（`^BY`）。

**支持 27 种条码制式**（`BARCODE_SPECS`，每种按手册 `Format: ^B…` 行确定字母位序）：
code11(`^B1`)、code128(`^BC` p.94)、code39(`^B3`)、code93(`^BA`)、i2of5(`^B2`)、planet(`^B5`)、
postnet(`^BZ` t=0)、usps(`^BZ` t=3)、upsmaxicode(`^BD`)、pdf417(`^B7`)、micropdf417(`^BF`)、
codabar(`^BK`)、msi(`^BM`)、plessey(`^BP`)、logmars(`^BI`)、std2of5(`^BJ`)、ean13(`^BE`)、
ean8(`^B8`)、upca(`^B9`)、upce(`^B0`)、upcext(`^BS`)、gs1databar(`^BR`)、code49(`^B4`)、
codablock(`^BB`)、datamatrix(`^BX`)、qrcode(`^BQ` p.128)、aztec(`^BO`)。

各制式专属参数（不通用，位置也不同）：

| 制式 | 专属键 | 说明 |
|---|---|---|
| `qrcode` | `scale`(c) `ec_level`(d) `model`(b) `mask`(e) `input_mode` | 纠错级与输入模式会写进 `^FD` 前缀 |
| `datamatrix` | `module_size_dots`(h) `quality`(s) `columns`(c) `rows`(r) | quality 只能 0/50/80/100/140/200 |
| `pdf417` | `security_level`(s) `columns`(c) `row_ratio`(r) | columns=1 为自适应 |
| `micropdf417` | `mode`(m) 0–33 | **不是** PDF417 的 s/c/r |
| `gs1databar` | `symbology_type`(b) `magnification`(c) `separator_height`(d) `height_mm`(e) `segment_width`(f) | 高度在 `e` 位，不在 `h` |
| `code49` | `interpret_pos`(f) `mode`(m) | f 只接受 N/A/B |
| `codablock` | `security`(s) `columns`(c) `rows`(r) `mode`(m) | mode ∈ A/E/F |
| `codabar` | `start_char`(k) `stop_char`(l) | A–D |
| `upsmaxicode` | `mode`(m) `symbol_number`(n) `total_symbols`(t) | 无 orientation 参数 |
| `msi` | `check_digit`(e) `insert_check_digit`(E) | e ∈ A/B/C/D |

数据规则（i2of5 偶数位、upca 11+校验、postal 纯数字等）builder 会以 `issues` 返回；
制式总表可用 `zpl_knowledge(mode="barcodes")` 查，命令语法用 `zpl_knowledge(mode="command")` 查。

**ZPL 发射规则**（全部经手册核对，也是与"凭记忆写 ZPL"的模型对话时最常纠正的点）：
- 条码命令按 **Format 行字母顺序**逗号分隔发射，尾部缺省省略、中段缺省留空位——合法形如 `^BCN,,Y`。
- 字段坐标 `^FO` 必须先于条码/数据命令发射（p.201）——否则多条码全部叠在原点。
- hex 转义生效时在 `^FD` 前发 `^FH`。
- **`^BQ` 的 `^FD` 必须以 `<纠错级><输入模式>,` 开头**（p.129 写死 Mandatory）：`^FDQA,数据`。
  漏掉前缀打印机把数据前两个字符当开关吃掉——实测 `^FDhttps://…` 解出来是 `ps://…`（少 `htt`）。

### 4.2 `validate_zpl(zpl) -> {issues[], ok}`

静态校验四层（`ok = 无 error`）：
1. **先抹掉 `^FD…` 数据段再扫命令**——标签内容里出现的 `~`、`^`（如 `100~200 SALE`）不是命令，不能被误报；
2. 提取 `^XY`/`~XY`/`^A@` token，对照 **253 条手册命令目录**，未命中 → `UNKNOWN_CMD`（warning）；
3. `^BQ` 的 `^FD` 不是 `<纠错级><输入模式>,` 开头 → `QR_FD_PREFIX`（error，p.129）——
   这是"语法全对但码扫不出来"唯一能在静态检查里抓住的错；
4. `^XA`/`^XZ` 配对（error，p.370/375）；payload 完全没有命令 → warning。

干净 ZPL 零误报（`^ZZ` 这类冷命令也在目录内——目录来自手册全文抽取，不是手写白名单）。

### 4.3 `zpl_knowledge(mode="command", query="")`

| mode | 查什么 | 例 |
|---|---|---|
| `command`（默认） | 单条命令的语法/默认值/范围/页码；查不到时给 `similar` 建议 | `query="^B9"` |
| `search` | 在命令名称+notes+syntax 上正则搜（最多 25 条） | `query="darkness|speed"` |
| `barcodes` | 27 种制式的数据规则 + `supported_by_generate_zpl` + `^BY` 默认值 | 空 query |
| `sgd` | Link-OS SGD 命令目录（可按名字过滤） | `query="calibration"` |
| `status` | `~HS` 状态码表 | `0000=READY, 0002=HEAD OPEN, 0003=PAPER OUT, 8000=TOO HOT…` |
| `list` | 全部 253 条命令 token | 空 query |

`COMMAND_NOTES` 共 69 条：手写的通用命令（`^FO`/`^A`/`^CI`/`~DG`…）+ **由 `BARCODE_SPECS` 自动生成的
条码命令页**（页码与 Format 行同源，避免"能发射、查不到"）。

### 4.4 `list_printers()`

PowerShell `Get-Printer` 枚举（Name/PortName/DriverName/PrinterStatus/Shared）。
用 PowerShell 而非 ctypes winspool **枚举**的原因：win32print 在部分 Python 构建里直接段错误（实测 exit 139）——
但**发送**相反，ctypes `WritePrinter` 是唯一可靠的 RAW 通道（见 §4.5）。
命令前缀 `[Console]::OutputEncoding=UTF8`，否则中文队列名在 powershell.exe 输出里会变成乱码，`_find_printer` 匹配不上。

### 4.5 `send_zpl(zpl, transport_kind, dry_run, host, port, printer, preview_name)`

| transport | 通道 | 关键点 |
|---|---|---|
| `tcp` | `host:9100` raw | Zebra 标准 raw 口；发送后短暂收 echo（9100 通常不回显，有 echo 即错误文本） |
| `windows` | winspool `WritePrinter`（`DOC_INFO_1.pDatatype="RAW"`） | 驱动无关地把原始字节丢进队列；返回 `job_id`、`bytes_sent`、`zebra_zpl_driver_detected` |
| `usb` | 实为 Windows 队列（USB 打印必过队列） | 找不到队列时返回可用队列列表 |
| `preview` | 写 `%TEMP%\zebra-mcp\label_preview.zpl` | 可贴 labelary.com 渲染核对 |
| `dry_run=true` | 只 validate + ASCII 预览 | 不发送 |

返回值统一 `{ok, transport, bytes_sent, response, hint}`，失败带诊断 hint。

### 4.6 `printer_status(host, port, sgd)`

- 不传 `sgd` → 先 SGD `! U1 getvar "$device.serial_number"`；**失败自动回退原生状态命令**：
  `~HI`（打印机识别：型号/固件/dot 密度/内存）+ `~HS`（状态：接口类别、缺纸/暂停/缓冲/温度各位、
  标签长度、状态码 `0000 = PRINTER READY`）。回退结果带 `note` 说明为什么回退。
  返回体里 `identification` 是 `~HI` 解析结果，`status` 是 `~HS` 第 3 行解析出的状态字
  （`{code, name, level}`，如 `{"code":"0000","name":"PRINTER READY","level":"ok"}`），`native_status` 保留原始回包。
- 传 `sgd` → 原样下发 `! U1 …`（getvar/setvar/do），`SGD_COMMANDS` 目录是查询起点。

### 4.7 `print_xlsx(xlsx_path, name_column, id_column, ...) -> {labels, rows_read, issues[], zpl_file, first_label, sent}`

表格 → 标签的整段封装，实现放在 `xlsx_batch.py`（185 行），命令行孪生脚本是 `scripts/xlsx_labels.py`。

**读表不引依赖**：`.xlsx` 就是个 zip，`xl/sharedStrings.xml` 存文本、`xl/worksheets/sheetN.xml` 存
`<c r="B3" t="s"><v>12</v></c>` 这样的单元格。`read_xlsx_rows()` 用 `zipfile` + 正则解析，两种写法都支持
（`t="s"` 共享字符串、`t="inlineStr"` 内联），数字单元格去掉 `.0` 尾巴。**不支持 `.xls`**（那是 OLE 复合文档）。

**列定位**三种写法等价：`"B"`（Excel 字母）、`2`（1 基序号）、`"员工号"`（表头文字，首行大小写不敏感）。

**居中怎么做的——两种不同机制**：

| 元素 | 机制 | 说明 |
|---|---|---|
| 姓名（文本） | `^FB{width},1,0,C,0` | 打印机用真实字体度量居中，CJK/ASCII 混排都准（p.186 的 alignment 参数） |
| 工号（Code128） | 算术居中：`x = (^PW − 11×(n+2)+13 × m) / 2` | `^BC` 没有对齐参数，只能算；模块数按 Code Set B 计，`m` 是 `^BY` 模块宽 |

工号 5 位、模块宽 3 dots 时：`(11×7+13)×3 = 270` dots，50 mm 标签（400 dots）居中 → `x=65`。
实测渲染后墨迹中心 199.5 px、图宽 399 px，分毫不差。

**为什么整批一个流**：`send_tcp` 每次都要等打印机回显（≈1 s）。N 张逐张发 = N×1 s 的纯等待；
拼成一个流（每张一个 `^XA…^XZ`）就只付一次，5 张实测 **1.03 s**。行数多了瓶颈会落到打印机物理速度上。

---

### 4.8 `zpl_from_image(image_path, width_mm, height_mm, dpi, frame, use_ocr, dry_run, host) -> {ok, zpl, elements[], decoded[], warnings[], validation, symbol_count}`

一条照片→ZPL 的完整重建流水线，全部在本仓库内（`photo.py` 345 行 + `ocr.py` 242 行），不依赖外部工程。

```
照片 ──[找标签/透视校正]──> 正视图 ──[zxing-cpp]──> 符号(格式+内容+框)
                                    └─[PP-OCRv4]──> 文字(内容+置信度+框)
        ──[px→dot 映射 + 逐元素规则]──> label spec ──[builder]──> ZPL
```

| 阶段 | 实现 | 关键参数/判据 |
|---|---|---|
| 标签定位 | Otsu 二值（正反两极性）→ 外轮廓 → `approxPolyDP` 四边形 → 单应变换 | 面积 ≥ 画面 8% 且 ≤ 92%，长边归一；失败退整图 + `NO_QUAD` |
| 符号识别 | `zxingcpp.read_barcodes(try_harder=True)` | 返回 `format`/`text`/四边形；一维码另测条高 |
| 条高测量 | 框内逐行 ink ratio ≥ 0.30 的连续带 | 取包含框中心的最长带，剔掉 HRI 文字行 |
| 文字识别 | DB det(736 长边, 0.3/0.5/1.5) + CTC rec(48 高, 右边填充) | `unclip` 用 pyclipper 真实偏移——按质心缩放近似会把 `NANJING` 读成 `NAN-ING` |
| HRI 合并 | 一维码下方 gap ≤ 1.2×条高、水平重叠 ≥30%、相似度 ≥50% | 必须按位置匹配：OCR 读 `ABC123` 得 `ABC1Z3`，按字面比对就会把这行打两遍 |
| 几何映射 | `dot = px × 标签宽(dot) / 照片宽(px)`，均匀缩放 | 给 `width_mm/height_mm` 才有比例尺；纵横比偏差 >15% 报 `ASPECT` |
| 模块宽/放大 | 一维码：模块数（Code128 = `11×n+13`）→ `round(框宽/模块数)`，下限 2 dots；二维码：`round(框宽/模块数)` | <2 dots 会加宽并警告：203dpi 下过细的条会被读成 Code39 |
| 文字尺寸 | `size_dots = 框高 / 0.775`，再按宽度约束 `0.51×size×字符数 ≤ 框宽` 收窄 | 非 ASCII 走 `font_file`（默认 `E:SIMSUN.TTF` → `^A@`） |

返回值里 `elements[]` 是给人复核的（`kind/format/text/dots/module_width/bar_height/hri`），`validation` 是
`builder.validate_zpl` 的原样结果，`scale` 是本次 px→dot 的比例。

识别通道二选一（`source` 字段会告诉你是哪条）：

| 参数 | 缺省 | 说明 |
|---|---|---|
| `llm_url` | 空 → `ZEBRA_VISION_URL` | OpenAI 兼容端点，写到 `/v1` 即可（自动补 `/chat/completions`） |
| `llm_key` | 空 → `ZEBRA_VISION_KEY` | 只用于一次 `Authorization: Bearer`，不回显/不落盘/不入返回值 |
| `llm_model` | 空 → `ZEBRA_VISION_MODEL` → `qwen2.5-vl-7b-instruct` | 多数端点必须显式给自己的模型名 |
| `vision_timeout` | 60 s | 单次识图请求超时 |

模型只负责"字在哪、写的是什么"：返回 0–1 归一化框，像素→dot 仍在 `photo.py` 里换算。条码内容仍以
zxing 为准，模型给的值仅在"一个都没解出来"时兜底并标 `BARCODE_UNVERIFIED` + `unverified: true`。
HTTP 错误按 401/403/404/413/429 分类给提示，响应体截 400 字符并把 key 替换成 `***`。

依赖是**可选 extras**：`pip install -e ".[photo]"`（opencv-python-headless / numpy / zxing-cpp /
onnxruntime / pyclipper）。OCR 权重 15 MB 不入库：`ZEBRA_OCR_MODELS` 指目录，或放 `data/ocr/`
（`ch_PP-OCRv4_det_infer.onnx`、`ch_PP-OCRv4_rec_infer.onnx`、`ppocr_keys_v1.txt`）。没装/没模型时
`use_ocr=True` 会给警告并只出符号与图形，不会崩。

实测（样例 480×320 px → 60×40 mm / 203 dpi）：Code128 `^FO30,21 … ^BCN,75`、QR `^FO379,24 ^BQN,,3`、
文字 `^FO296,231 ^A0N,43`；与既有参考实现（Android 端 Port）的像素测量逐值一致；把结果送 Labelary
8dpmm 渲染再 zxing 解码，`ABC123` / `NGSU` 原样读回。耗时：冷启 0.45 s（装模型），热 0.14 s。

---

### 4.9 `render_zpl(zpl, out_path="", scale=1) -> {ok, png, dots[], drawn[], approximated[], ignored[], warnings[]}`

离线渲染，把"版式对不对"变成可判断的像素。**没有调用任何云服务**——Labelary 只在开发期当标尺用，运行时零网络依赖。

实现是单遍状态机（`render.py`，约 330 行）：

1. 按 `([\^~])([A-Z@][A-Z0-9]?)` 切 token，参数串跟着命令走；`^FD` 的数据就是到下一个 `^` 为止的原文，所以数据里的 `^`/`~` 不会撕碎语法；
2. 维护标签级状态：`^FO/^FT` 光标、`^BY`（模块宽/比例/条高）、当前字体高宽、`^FB`（块宽/行数/行距/对齐）；
3. 反向表 `_CMDS = {spec["cmd"].lstrip("^"): key}` 把 `^BC`/`^BQ` 映射回 `BARCODE_SPECS`，参数位序直接复用 builder 的定义——**一份规格，两处使用**，不会出现"生成用一套位序、渲染用另一套"；
4. 每个 `^FD` 按当前 pending 元素落笔：文本走 Pillow（横向压缩到 `^A0` 宽度），一维码走 python-barcode 的模块串逐 run 画矩形，QR 走 segno 的矩阵逐模块画格。

三个校准常数不是拍脑袋，是对着 Zebra 自己的渲染逐像素量的（同一段 ZPL，8 dpmm，60×40 mm）：

| 量 | Zebra | 本渲染器 | 处理 |
|---|---|---|---|
| `^A0N,43` 的 `NANJING` | 153×34 dots | 156×33 | 字号取 `h`、墨迹高即帽高；横向不再压 |
| Code128 101 模块 × `^BY3` | x 30…332 | x 30…332 | 逐 run 画矩形，无取整误差 |
| `^BQN,,3` 的 21 模块二维码 | x 379…441 | x 379…441 | 原点偏移 1×3 模块（实测，非 4 模块静区） |

**故意不做的事**：不仿真打印机字体字形、不模拟静区对扫描的影响、不实现对没有离线编码器的制式（`approximated[]` 里点名）。渲染器一旦开始"猜得像"，就会给出比实际更好看的假象——那比不渲染更危险。

## 5. 实机数据：ZR668-200dpi 全链路记录

| 项 | 实测值 |
|---|---|
| 识别（`~HI`） | `ZR668-200dpi, V86.20.21Z, 8 dots/mm, 8192KB` |
| SGD 兼容性 | **getvar 返回 `?`** —— V86.20.21Z 属 Fusion 系 pre-Link-OS，无完整 SGD |
| 状态（`~HS`） | 接口 159（网络）；缺纸/暂停/缓冲满/温度全 0；标签长 315 dots ≈ 39.4 mm；`0000 = PRINTER READY` |
| TCP 打印 | 多张 Code128+QR+文本标签，`send_tcp` `ok=True, 105 bytes`，零错误回显 |
| MCP 端到端 | `generate_zpl` → `send_zpl(tcp)` 中文标签 `ok=True, 234 bytes` |
| 复检后复打 | 中文(`^A@N,28,28`)+QR+`^B9R,63`+边框 → `ok=True, 195 bytes`，零回显，`~HS 0000` |
| 耗时 | `generate_zpl` ≈0.1 ms、`validate_zpl` ≈0.15 ms、`send_zpl(tcp)` ≈1.0 s、`printer_status` 13.4 s → **3.9 s**（改空闲窗后） | 全部单机实测 |
| 扫码闭环 | 文档示例标签 8dpmm 渲染 → zxing 解出 Code128 `ABC12345` + QR `https://example.com/p/1` | 静区修正后 |
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
| 单元 | `tests/test_builder.py`：31 组断言——默认值/范围、27 种制式的 Format 位序、QR `^FD` 前缀、`^A@` 中文、`^FH` 转义、`^FB`、图形、编码仲裁、validate 正反例（`^QQ` 报 UNKNOWN、`^ZZ0` 不报、`^FD` 数据不误报）、validate 单独可用（首次调用自装命令目录）、xlsx 读表/列解析/Code128 居中/多标签单流、逐轴坐标不回零、照片→ZPL（解码内容/几何 ±4 dot/`^BY` 逐字段/校验器回环）、离线渲染几何（条码 x30…332、QR x379…441、`^A0N,43` 墨迹 153±4×33±2 全与 Zebra 逐值一致） | `ALL TESTS PASSED` |
| 协议 | stdio 探针（initialize→initialized→tools/list→tools/call 逐条驱动） | 6 工具全通 |
| 语义/渲染 | Labelary 渲染 PNG → opencv `QRCodeDetector` 解码（本地没有扫枪时的等价验证） | `^FDQA,…` 解出原串；旧写法 `^FDhttps://…` 解出 `ps://…`（少 `htt`） |
| 实机 | TCP 9100 打印、`~HI/~HS`、web 目录、中文字体对比标签 | 全部通过（ZR668） |
| 编译 | `py_compile` 全模块 | 通过 |

---

## 10. 已知边界与路线图

- **老固件**（Fusion 系）：SGD 基本不可用，依赖原生 `~` 命令回退；更老的 Xi4 系部分命令不支持。
- **Windows USB raw**：走队列的 `RAW` 数据通道（`WritePrinter`），驱动收到 RAW 会原样下传；`job_id`/`bytes_sent` 只能证明**入队成功**，纸张是否真出取决于物理连接，核对仍以 TCP 或肉眼为准。
- **渲染预览**：`render_zpl` 覆盖本工具生成的元素子集（文本/主流一维码/QR/几何图元，见 §4.9），像素级校准到 Zebra `^A0` ±2%；DataMatrix/PDF417/Aztec/MaxiCode 等无离线编码器的制式只画占位框。ASCII 预览仍是"没装 pillow 时的兜底"（dot→字符格 ÷6/÷12）。
- **未做**：ZPL 二进制格式（`^D`/`~DY` 字体下载通道已有知识、未封装成工具）；`~DY` 下载工具值得下一步补上
  （对没有内置 CJK 的机型有用）。
- **扩展方向**：`^DF` 格式调用、`^HC` 目录读取、DataMatrix/PDF417 的离线编码（渲染与生成都要）、SGD 变量全量 dump 解析。

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
12. **`^BQ` 的 `^FD` 必须以 `<纠错级><输入模式>,` 开头**（p.129 明确写 Mandatory）：`^FDQA,https://…`。漏掉前缀，标签上的二维码扫不出来或干脆空着——这是最隐蔽的错，因为 ZPL 语法校验不会报错。
13. **参数位序要看各自手册 Format 行，不能套用 `^BCo,h,f,g` 的习惯**：`^B9o,h,f,g,e`、`^B0o,e,h,f,g`、`^BRa,b,c,d,e,f`、`^BQa,b,c,d,e`、`^BFo,h,m`。把 orientation 塞进不存在的 `o` 位（或按别的制式顺序拼 `a,c,b,d,e`），打印出来是校验位/列数乱序，甚至 KeyError 崩在服务端。builder 现在按 `BARCODE_SPECS[..]["letters"]` 逐制式发射。
14. **`Print-File` 不是通用 cmdlet**（本机 `Get-Command Print-File` 为空，只有 `Out-Printer`，而 `Out-Printer` 发的是文本）。Windows raw 打印只有一条正路：`winspool.drv` 的 `OpenPrinter → StartDocPrinter(RAW) → WritePrinter`。
15. **多行 SGD 响应不能"读到第一个 `\r\n` 就停"**——`! U1 getvar "$~"` 是几百行，早停只拿到第一行；改成短空闲超时（0.6s）读到打印机不再吐字节。
16. **`^A@` 的宽度位别留空**：`^A@N,28,,E:SIMSUN.TTF` 在某些固件上按 0 处理（字形退化成极窄），显式给 `o,h,w` 才确定；CJK 用方形字形，宽度=高度即可。
17. **知识库与构建器必须同源**：条码命令的页码/Format 行现在由 `BARCODE_SPECS` 生成 `COMMAND_NOTES`，新增制式只改一处，否则会出现"能发射、`zpl_knowledge` 查不到"。
18. **`validate_zpl` 通过 ≠ 码能扫**：命令名、配对、参数范围都查不出语义错（QR 前缀、静区都是典型）。静态校验只能抓形式，语义要靠"渲染 + 解码"闭环——本项目的做法是 Labelary 渲染 PNG 后用 zxing/opencv 解一遍，和打印出来的码等价。
19. **长超时会惩罚每一次状态查询**：原生 `~HI`/`~HS` 原来用 5 s socket 超时读全，打印机答完也不关连接，
    于是每次 `printer_status` 白等 2×5 s——实测 **13.4 s**。改成"收到首包后切 0.5 s 空闲窗"，降到 **3.9 s**。
    凡是"读到连接空闲为止"的读法，首包用长超时、后续用短空闲窗。
20. **静区（quiet zone）不归语法管**：一维码两侧需 ≥10 倍模块宽的空白。实测 Code128 放在离边框 1 mm 处，
    ZPL 语法完全正确、渲染出来的条码**格式无误但扫码器读不出**；挪到 6 mm 立刻可读。
    `^BY` 默认模块宽 2 dots（0.25 mm）已是临界值，零售/物流标签建议给 3。
21. **OCR 会把 HRI 读错一个字，而这条错误会长成两张标签**：一维码下面那行 `ABC123`，OCR 给出 `ABC1Z3`
    （置信度 0.89 还挺高）。如果拿它跟解码结果做字面比对，判"不相等"就成了另一段文字，最后标签上这行
    打两遍——一遍来自 `^BC …,Y`，一遍来自文本字段。必须按**位置**判：正下方、gap ≤1.2×条高、
    水平重叠 ≥30%，再用 50% 相似度兜底。
22. **`^BY` 是打印机级状态，不是字段属性**：一张标签上 128 码要模块宽 3、二维码要 magnification 3，
    只在标签开头发一次 `^BY` 会把两者绑死（条码变窄或 QR 溢出）。逐字段发包前 `^BY` 才是对的——
    顺带也是照片重建的唯一可行解，因为每个符号的物理宽度都不一样。
23. **ONNX rec 模型的输入宽度不是随便给的**：`48×W` 里 W 由 `ceil(48×宽高比/8)` 得来，超宽文本行
    会让模型内部形状对不上（`Non-zero status code returned while running Add`）。做法是逐裁剪按比例
    计算并夹在 `[32, 320]`，失败再退一次 320 宽的标准画布。另一个更隐蔽的同源坑：`load()` 返回
    `(det, rec)`，两行写反了会拿检测模型跑识别——报错信息完全指向 ONNX，不指向自己。

---

24. **`segno` 会悄悄生成 Micro QR**：`NGSU` 这种短数据默认给 17 模块的 Micro QR，而 Zebra `^BQ` 只认标准 QR。渲染出来比真机小一圈，且真机根本不认。必须 `segno.make(..., micro=False)`，回归断言锁"QR 宽度 = 21 × 模块宽"。

25. **ZPL 字体高度不是 PIL 的字号**：`^A0N,43` 在真机上帽高 33 dots，直接 `ImageFont.truetype(size=43)` 画出来是 43。差 30% 意味着预览会把"刚好放得下"的字画成"放不下"。正确做法是量 ink bbox 校准（本仓库取"字号 = h、墨迹即帽高"），并用同一段 ZPL 与 Labelary 逐值对照锁死。

26. **`BARCODE_CMDS` 的值是元组，不是命令字符串**：它是 `symbology -> (cmd, 人类可读名)`，`spec["cmd"]` 还带 `^` 前缀，而分词得到的 token 不带。渲染器最初用 `.values()` 判成员、用带 `^` 的键查表，结果**所有条码静默变成"未知命令"跳过**——页面上只剩文字，看起来像"这条码没画"。修法是反查表 `{spec["cmd"].lstrip("^"): key}`。

## 12. 源码导览

| 文件 | 行数 | 核心内容 |
|---|---|---|
| `src/zebra_mcp/server.py` | 437 | FastMCP 入口、9 工具、SGD↔原生状态回退逻辑（含 `~HS` 状态字解析） |
| `src/zebra_mcp/builder.py` | 713 | `build_label`（spec→ZPL）、`validate_zpl`、27 制式 `BARCODE_CMDS`/`BARCODE_SPECS`（逐制式 Format 字母位序）、`^A@` font_file、默认值/范围校验（带页码） |
| `src/zebra_mcp/transport.py` | 257 | `_clean_zpl` 编码仲裁、`send_tcp`、`list_windows_printers`、`send_windows_printer`（winspool RAW）、`ascii_preview`、`write_preview` |
| `src/zebra_mcp/knowledge.py` | 337 | `COMMAND_NOTES`、`BARCODE_NOTES`、`SGD_COMMANDS`、`STATUS_NOTES`、`known_commands()` |
| `src/zebra_mcp/catalog.py` | 15 | 把 253 命令目录注入 builder 校验器 |
| `src/zebra_mcp/photo.py` | 400 | 照片→spec：标签定位/透视校正、zxing 解码、条高测量、HRI 按位置合并、px→dot 映射 |
| `src/zebra_mcp/vision.py` | 179 | 可选视觉 LLM 通道：OpenAI 兼容请求、归一化框解析、key 不回显、错误分类 |
| `src/zebra_mcp/ocr.py` | 242 | PP-OCRv4 det+rec（ONNX）：DB 后处理（阈值 0.3 / unclip 1.5）、CTC 解码、宽度夹取 |
| `tests/test_builder.py` | 479 | 31 组断言（制式位序、QR 前缀、`^A@`、`^FH`、编码仲裁、预览、知识库覆盖、xlsx 批量、照片→ZPL 几何与回环、渲染几何对照） |
| `src/zebra_mcp/render.py` | 350 | 离线 ZPL→PNG 渲染（状态机、`^A0` 校准、1D/QR 绘制、未支持制式点名） |
| `tests/fixtures/photo_sample.png` | — | 照片→ZPL 回归样张（480×320，Code128 `ABC123` + QR `NGSU` + `NANJING`） |
| `scripts/mcp_probe.py` | 61 | 裸 JSON-RPC stdio 探针（不依赖 MCP SDK，一次一请求） |
| `src/zebra_mcp/xlsx_batch.py` | 185 | 标准库读 xlsx、列定位、行→spec、Code128 宽度算居中、多标签单流 |
| `scripts/xlsx_labels.py` | 64 | `print_xlsx` 的命令行孪生（dry-run 默认，`--execute` 才发） |
| `scripts/extract_manual.py` | 78 | 从 ZPL II 手册 PDF 重建 `data/` 派生文件（不入库，版权原因） |
| `data/zpl_manual_full.txt` | 44,030 | ZPL II 手册全文（一切默认值的出处） |

---

*文档生成于 2026-10-22，同日全量复检后修订（条码位序、QR 前缀、RAW 通道、校验器四层）；
所有实测数据来自一台 ZR668-200dpi 实机（局域网 9100）、MCP stdio 探针，以及 Labelary 渲染 + opencv 解码验证。*
