# Zebra 打印 MCP · 详细功能说明

> 版本 0.3.0 ｜ stdio MCP server ｜ 9 个工具 ｜ 253 条命令 ｜ 27 种条码制式 ｜ 31 组回归断言
> 所有数字与默认值均从代码与 ZPL II 编程手册（zpl-zbi2-pg-en-202609）取，不是估计值。

一句话：**模型只填结构化字段，ZPL 的语法、默认值、参数位序、单位换算、编码由服务端保证。**

---

## 1. 运行形态

| 项 | 值 |
|---|---|
| 协议 | MCP over **stdio**（不是 HTTP；要远程调用得自己包一层网关） |
| 入口 | `python -m zebra_mcp.server`（或控制台脚本 `zebra-printer-mcp`） |
| 传输 | TCP 9100 raw / Windows 打印队列（RAW）/ USB 队列 / 纯文件预览 |
| 平台 | Windows 实测（ZR668-200dpi，固件 V86.20.21Z）；Linux/macOS 除 windows/usb 通道外通用 |
| 依赖 | 核心只有 `mcp>=1.9,<2`；照片→ZPL 需 `[photo]` extra |
| 受控数据 | 253 条命令目录、27 种条码制式表、13 个状态码、27 条 SGD 命令 |

---

## 2. 九个工具：输入 → 输出

### 2.1 `generate_zpl(label_spec: dict)`

**输入**：一个 label spec（§3 完整键表）。
**输出**：

| 字段 | 类型 | 说明 |
|---|---|---|
| `zpl` | string | 完整 ZPL 流（`^XA…^XZ`） |
| `issues[]` | array | 每条 `{severity: error/warning, code, field, message}`，带手册页码 |
| `preview_file` | string | 落盘的 `.zpl.preview`（ASCII 近似版式，不是像素渲染） |
| `ascii_preview` | string | 同上内容的文本形态 |

`issues` 里出现 `error` 时仍会返回 ZPL（便于对照），但**不要直接打**。

### 2.2 `validate_zpl(zpl: str)`

**输入**：任意 ZPL 文本（可多标签、可含中文）。
**输出**：`{issues: [...], ok: bool}`。四层检查：

1. 先抹掉 `^FD…^FS` 数据段（数据里的 `~`、`^` 不当命令，`100~200` 不会误报 `~20`）；
2. 逐 token 比对 253 条命令目录（拼写错 → `UNKNOWN_CMD`）；
3. 语义规则（QR `^FD` 前缀、EAN/UPC 数据位、坐标越界、`^CI` 与 CJK 是否匹配等）；
4. `^XA`/`^XZ` 配对。

注意：**静态校验通过 ≠ 码能扫**（静区、模块宽只有渲染+解码能验，见 §7）。

### 2.3 `zpl_knowledge(mode: str = "command", query: str = "")`

| mode | 作用 | query 示例 |
|---|---|---|
| `command` | 单条命令的语法、默认值、范围、手册页码 | `^BY`、`^BQ`、`~DG` |
| `search` | 在全部命令笔记上做正则搜索 | `darkness\|speed` |
| `barcodes` | 27 种制式的参数位序与数据规则 | 留空列全部 |
| `sgd` | Link-OS SGD 的 setvar/getvar/do 示例（27 条） | `calibration` |
| `status` | ZBI II 状态/错误码（13 条，含 `0000 = PRINTER READY`） | 留空列全部 |
| `list` | 全部命令 token | — |

**输出**：JSON（命令条目含 `syntax / default / range / page`；状态码含 `code / name / level`）。

### 2.4 `list_printers()`

**输入**：无。
**输出**：`[{name, port, driver, status, shared}, ...]`，无名/错误条目会被过滤，不会把错误对象混进列表。

### 2.5 `send_zpl(zpl, transport_kind="tcp", host="", printer="", port=9100, dry_run=False)`

**输入**

| 参数 | 默认 | 说明 |
|---|---|---|
| `zpl` | — | 要发的 ZPL |
| `transport_kind` | `tcp` | `tcp` / `windows` / `usb` / `preview` |
| `host` | 空 | TCP 时必填（打印机局域网 IP） |
| `port` | 9100 | raw 端口 |
| `printer` | 空 | windows/usb 时填队列名 |
| `dry_run` | false | 只校验+落盘预览，不发送 |

**输出**：`{validation, preview_file, ok, transport, bytes_sent, response}`（`dry_run` 时只回 `validation` + `preview_file` + `dry_run: true`）
`response` 是打印机回显——**空回显 = 没报 `^XA` 语法错**；windows/usb 只到「入队成功」，`job_id` 不代表纸出来了。

### 2.6 `printer_status(host, port=9100, sgd="", timeout=5.0)`

**输入**：`sgd` 为空时默认跑 `! U1 getvar "$device.name.custom"` + `device.ip.address` + 状态轮询。
**输出**：`{identification, status, sgd{}, native_status}`

- `identification`：来自 `~HI`，形如 `ZR668-200dpi,V86.20.21Z,8,8192KB`（型号, 固件, 每毫米点数, 内存）
- `status`：`~HS` 第三行的状态字，`0000 = PRINTER READY`
- `native_status`：结构化 `{code, name, level}`
- 老 Fusion 固件（`! U1 getvar` 回 `?`）自动回退原生 `~HI`/`~HS`
- 读法：首包长超时 + 后续 0.5 s 空闲窗（实测 3.9 s，早期 13.4 s）

### 2.7 `print_xlsx(xlsx_path, name_column="A", id_column="B", sheet="", host="", port=9100, width_mm=50, height_mm=30, name_size_mm=4, barcode_height_mm=10, module_width=3, skip_header=True, max_labels=2000, dry_run=False)`

**输入**：`.xlsx/.xlsm` 路径 + 两列定位（Excel 字母 `"A"` / 1 基序号 `"1"` / 表头名 `"姓名"`）。
**输出**：`{labels, rows_read, columns:{name,id}, bytes, issues[], zpl_file, first_label}`，实际打印时多 `sent`，dry-run 时多 `dry_run` + `hint`；被跳过的行 = `rows_read − labels`

规则：首行当表头（`skip_header=false` 可关）；**工号列为空的行跳过**；姓名用 `^FB …,C` 由打印机居中，条码按 `11×(n+2)+13` 模块算宽度算术居中；整批拼成**一个 ZPL 流、一次 TCP**（5 张 ≈1.0 s）。

### 2.8 `zpl_from_image(image_path, width_mm=60, height_mm=40, dpi=203, frame="auto", use_ocr=True, font_file="E:SIMSUN.TTF", host="", port=9100, dry_run=True, llm_url="", llm_key="", llm_model="", vision_timeout=60, paper_mm=0, paper_height_mm=0, offset_x_mm=0, offset_y_mm=0)`

**输入**：标签照片 + **物理尺寸**（像素→点的唯一比例尺）；可选视觉大模型通道（OpenAI 兼容，`ZEBRA_VISION_URL/_KEY/_MODEL` 环境变量兜底）。
**输出**：`{ok, zpl, elements[], decoded[], warnings[], issues[], validation, zpl_file, source, scale, photo_px, label_dots, symbol_count, text_count, sent?}`

- `source`：`local_ocr` / `vision` / `none`
- `elements[]`：`{kind, format, text, dots[x1,y1,x2,y2], module_width?, bar_height?, magnification?, hri?, conf?}`
- 条码内容**永远以 zxing 解码为准**；模型给的值只在「一个都没解出来」时兜底，并标 `unverified: true` + `BARCODE_UNVERIFIED`
- 单模块不足 2 点会被加宽并警告（203 dpi 下过细的条会被读成 Code 39）

---

### 2.9 `render_zpl(zpl: str, out_path: str = "", scale: int = 1)`

把 ZPL 渲染成 PNG，让 agent 在花纸之前先看一眼。**纯本地**，不走 Labelary、不碰打印机，需 `pip install -e ".[render]"`。

| 参数 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `zpl` | str | 必填 | 任意 ZPL；尺寸优先取 `^PW`/`^LL`，缺失才回退 104×152 mm |
| `out_path` | str | `""` | 空则写到项目根 `render_preview.png` |
| `scale` | int | `1` | 1–3，仅放大 PNG 像素；`dots` 始终是真实点数 |

**输出**：`{ok, png, dots:[w,h], scale, drawn[], approximated[], ignored[], warnings[]}`

- `drawn[]`：实际画出的元素，`{type, text?, dots:[x1,y1,x2,y2], width?, size?, lines?}`，几何值单位是 dot，可直接与 spec 里的坐标对账；
- `approximated[]`：**画不准但必须告诉你的**（DataMatrix 等无离线编码器 → 占位框；`^FR` 反白按正常黑画；`^XGR` 图形 → 占位框）；
- `warnings[]`：绘制时发现的几何问题，目前最有价值的一条是"文本比 `^FB` 块宽"；
- `ignored[]`：认识但不影响页面的命令（`^MD`/`^PR`/`^MN`/`~SD` 等）。

**保真度（实测，与 Zebra 自己渲染的同一段 ZPL 逐值对照）**：

| 元素 | 保真度 | 依据 |
|---|---|---|
| `^A0` / `^A@` 文本 | 墨迹高度 ±1 dot，宽度 ±2% | `^A0N,43` + `NANJING`：本工具 156×33 dots，Zebra 153×34 |
| Code128 / Code39 / EAN-13 / EAN-8 / UPC-A / I2of5 / Codabar / GS1-128 | 模块级精确 | 逐模块按 `^BY` 模块宽绘制；`^FO30` 起、101 模块 × 3 = 303 dots，与 Zebra 完全一致 |
| QR | 模块级精确 | 模块宽 = `^BQ` 的 c；强制标准 QR（不生成 Micro QR）；原点偏移 1×3 模块 |
| `^GB` / `^GC` | 精确 | 矩形/椭圆按参数画 |
| DataMatrix / PDF417 / Aztec / MaxiCode / Code49 / Codablock / MSI / Code11 / Planet / Postnet / GS1 DataBar / UPC-E | **不画，占位框** | 无离线编码器；写入 `approximated` |
| 打印机内置字体字形 | 近似 | 用 host 上的 msyh/DejaVu 代替 `E:SIMSUN.TTF` 等；字宽已按 `^A0` 校准 |
| `^FR` 反白、`^XGR` 图形 | 近似 | 正常黑画 + 占位框，并列入 `approximated` |

**它不是 ZPL 仿真器**，用途很窄但很值：判重叠、判越界、判文本溢出、判条码与相邻元素挤压。**不能用它判断能不能扫**——静区、模块实际印刷宽度、纠错级这些只有真机和扫码枪能答。

## 3. label spec 顶层键（`generate_zpl` 入参）

尺寸单位默认 mm，带 `_dots` 后缀的键直接给点。

| 键 | 类型 / 取值 | 对应 ZPL | 说明 |
|---|---|---|---|
| `dpi` | 152 / 203 / 300 / 600 | — | 非主流值会警告（`DPI`） |
| `width_mm` | number | `^PW` | 打印宽度 |
| `height_mm` | number | `^LL` | 标签长度 |
| `print_width_dots` | int 2–PW_MAX | `^PW` | 直接用点（优先于 mm） |
| `label_length_dots` | int 1–LL_MAX | `^LL` | 同上 |
| `quantity` | int | — | 份数（由调用方循环或批量流实现） |
| `darkness` | 0–30 绝对 / −30–30 相对 | `~SD` / `^MD` | **0–30 走绝对 `~SD`**，`^MD` 是增量且跨任务累积 |
| `print_speed` `slew_speed` `backfeed_speed` | `2`… 或字母 | `^PR` | 只给速度时可只传一个 |
| `char_encoding` | 编码号 | `^CI` | 有 CJK 且未指定 → 自动 `^CI28`（警告 `CI_CJK`） |
| `barcode_defaults` | `{module_width, ratio, bar_height}` | `^BY` | 默认 2 / 3.0 / 10；**逐字段可覆盖** |
| `default_font` | `{font, height, width}` | `^CF` | 默认字体（不是 `^A6`） |
| `default_orientation` | N/R/I/B | `^FW` | 全局缺省方向 |
| `mirror` | bool | `^PM` | 水平镜像 |
| `invert` | bool | `^PO` | 反白整张 |
| `shift_left` | int dots | `^LS` | 整体左移 |
| `label_top` | int dots | `^LT` | 纵向整体偏移 |
| `media_tracking` | `{mode: N/Y/W/M/B/A, black_mark_offset}` | `^MN` | 连续纸/间隙/黑标/缺口 |
| `print_mode` | `{mode: T/P/R/A/K, media_loss}` | `^MM` | 热转印/热敏/剥离/切刀… |
| `fields` | array | — | 见 §4（顺序即打印顺序） |

---

## 4. fields：七种元素

`type` 取值：`text`、`barcode`、`note`（= `box`）、`circle`、`reverse`、`image`。

### 4.1 `text`

| 键 | 说明 |
|---|---|
| `data` | 文本；含 `~`/`^` 或非 ASCII 时自动做 `^FH` hex 转义（`no_hex: true` 关掉） |
| `x_mm`/`x_dots`、`y_mm`/`y_dots` | 原点坐标，**逐轴独立**解析（给 `x_dots` 不会让 `y_mm` 失效） |
| `font` | 内置字体 `0-9` / `A-Z`（`^A`） |
| `font_file` | 打印机内 TrueType，格式 `E:NAME.TTF`（`^A@`，中文必用，如 `E:SIMSUN.TTF`） |
| `size_mm` / `size_dots` | 字高；可缩放范围 10–32000 dots |
| `width_mm` / `width_dots` | 字宽（`^A@` 第三位，缺省=高） |
| `orientation` | N / R / I / B |
| `origin` | `FO` 绝对定位（默认）/ `FT` 相对上一文本字段 |
| `block` | `{width_mm, lines, line_height, alignment: L/C/R/J}` → `^FB` 多行块（自动折行、按真实字体度量居中） |
| `color` | 反白等颜色指令 |

### 4.2 `barcode`

通用键：`symbology`（§5 的 27 个 key）、`data`、`x_*`/`y_*`、`orientation`、`height_mm`/`height_dots`、`module_width`、`module_ratio`、`bar_height`（这三个会就地重发 `^BY`）、`no_hex`。
制式专属键见 §5 最后一列。

### 4.3 `note` / `box`

`{type:"note", x_mm, y_mm, width_mm, height_mm, thickness, color?}` → `^GB`（矩形框/填充）。`note` 与 `box` 是同一实现。

### 4.4 `circle`

`{type:"circle", x_mm, y_mm, diameter_mm|diameter_dots, thickness}` → `^GC`。

### 4.5 `reverse`

`{type:"reverse"}` → `^FR`（此后绘制的内容反白，直到 `^FS` 作用域结束）。

### 4.6 `image`

`{type:"image", name, device:"R"|"E"}` → `^XGR:<name>,1,1`，**引用打印机里已存在的图形**。
本项目**不提供** `~DG`/`^GF` 下载通道——位图请用 Zebra Designer/BarTender 先存进打印机。

---

## 5. 支持的 27 种条码

参数位序按各制式**自己的 Format 行**（不能套 `^BC` 的 `o,h,f,g,e`）。字母含义以 `zpl_knowledge(mode="command", query="^Bx")` 返回为准（含手册页码）。

| key | 名称 | 命令 | 参数位序 | 默认值 | 专属可选键 |
|---|---|---|---|---|---|
| `code128` | Code 128 / GS1-128 | `^BC` | o,h,f,g,e,m | o=N h=— f=Y g=N e=N m=N | `check_digit`、`mode`(N/U/A) |
| `code39` | Code 39 | `^B3` | o,e,h,f,g | o=N e=N h=— f=Y g=N | `check_digit` |
| `code93` | Code 93 | `^BA` | o,h,f,g,e | f=Y | `check_digit` |
| `code11` | Code 11 | `^B1` | o,e,h,f,g | f=Y | `check_digit` |
| `i2of5` | Interleaved 2 of 5 | `^B2` | o,h,f,g,e,j | f=Y | `check_digit` |
| `std2of5` | Standard 2 of 5 | `^BJ` | o,h,f,g | f=Y | — |
| `logmars` | Industrial 2 of 5 | `^BI` | o,h,f,g | f=Y | — |
| `codabar` | Codabar | `^BK` | o,e,h,f,g,k,l | k=A l=A | `start_char`/`stop_char` |
| `msi` | MSI | `^BM` | o,e,h,f,g,E | e=B E=N | `check_digit`、`insert_check_digit` |
| `plessey` | Plessey | `^BP` | o,e,h,f,g | f=Y | `check_digit` |
| `ean13` | EAN-13 | `^BE` | o,h,f,g | f=Y | 数据 12/13 位（校验位可自算） |
| `ean8` | EAN-8 | `^B8` | o,h,f,g | f=Y | 数据 7/8 位 |
| `upca` | UPC-A | `^B9` | o,h,f,g,e | f=Y e=N | 数据 11/12 位 |
| `upce` | UPC-E | `^B0` | o,e,h,f,g | f=Y | — |
| `upcext` | UPC/EAN 附加码 | `^BS` | o,h,f,g | g=Y | — |
| `planet` | Planet Code | `^B5` | o,h,f,g | f=N | 邮政 11/13 位 |
| `postnet` | POSTNET | `^BZ` | o,h,f,g,t | f=N t=0 | — |
| `usps` | USPS IM Barcode | `^BZ` | o,h,f,g,t | f=N t=3 | — |
| `pdf417` | PDF417 | `^B7` | o,h,s,c,r,t | s=0 c=1 r=3 t=N | `security`/`columns`/`rows` |
| `micropdf417` | MicroPDF417 | `^BF` | o,h,m | m=0 | `mode` |
| `code49` | Code 49 | `^B4` | o,h,f,m | f=N m=2 | `interpret_pos`、`mode` |
| `codablock` | CODABLOCK | `^BB` | o,h,s,c,r,m | h=8 s=Y m=A | `security`/`columns`/`rows`/`mode` |
| `datamatrix` | Data Matrix | `^BX` | o,h,s,c,r | s=200 c=0 r=1 | `module_size_dots`(1–25)、`quality`(0/50/80/100/140/200) |
| `qrcode` | QR Code | `^BQ` | a,b,c,d,e | a=N b=2 c=0 d=Q e=7 | `model`(1/2)、`scale`(1–10)、`ec_level`(L/M/Q/H)、`mask`(0–7)、`input_mode` |
| `aztec` | Aztec | `^BO` | a,b,c,d,e,f,g | a=N b=2 c=N d=0 e=N | — |
| `upsmaxicode` | UPS MaxiCode | `^BD` | m,n,t | m=2 n=1 t=1 | `symbol_number`、`total_symbols`（**无方向参数**） |
| `gs1databar` | GS1 DataBar | `^BR` | a,b,c,d,e,f | a=R b=1 d=1 e=25 f=22 | `symbology_type`/`magnification`/`separator_height`/`segment_width`（高度在 `e` 位） |

三个必须知道的细节：

- **QR 的 `^FD` 前缀由生成器自动补**：`^FD<纠错级><输入模式>,数据`。漏了打印机把数据前两位当开关吃掉（`https://…` → `ps://…`），且不报错。
- **`^BY` 是打印机级状态**：一张标签上 128 码与二维码模块宽不同时，必须在每个符号前重发。字段带 `module_width`/`module_ratio`/`bar_height` 即自动重发。
- **`^BR`（GS1 DataBar）方向默认是 R**，别的制式是 N——不显式给方向标签会悄悄转 90°。

---

## 6. 单位、坐标与排版规则

- mm→dots 一律走 `mm2dots(value, dpi)`（`dots = mm × dpi / 25.4`），203 dpi 下 1 mm = 8 dots。
- **逐轴解析**：`x_mm` 与 `y_dots` 可以混用；`_xy()` 每根轴独立判断，不会因为给了 `x_dots` 就把 `y_mm` 丢成 0。
- 坐标范围 0–FO_MAX，越界报 `POS`；`^PW`/`^LL` 超范围报 `PW`/`LL`。
- 文本居中用 `^FB{宽},1,0,C,0`（打印机按真实字体度量算）；一维码无对齐参数，只能算术居中。
- 静区（quiet zone）：一维码两侧需 ≥10 倍模块宽。模块宽 3 点 → ≥3.75 mm。实测离边框 1 mm 时语法全对、扫码器读不出。

---

## 7. 校验与问题码

`generate_zpl`、`validate_zpl`、`print_xlsx`、`zpl_from_image` 都返回 `issues[]` / `warnings[]`，每条 `{severity, code, field, message}`。

| 组别 | 代码 |
|---|---|
| 命令与结构 | `UNKNOWN_CMD` `NO_CMD` `BRACKETS` `FIELD_TYPE` `IMG_NAME` |
| 位置与尺寸 | `POS` `ORIGIN` `PW` `LL` `DPI` `ORIENT` `ORIENT_NA` `TEXT_SIZE` `BC_HEIGHT` `BY_WIDTH` |
| 字体与编码 | `FONT` `FONT_FILE` `CI_CJK` `BC_CJK` `HEX_NEEDED` |
| 条码语义 | `BARCODE_UNKNOWN` `QR_FD_PREFIX` `QR_MODEL` `QR_EC` `QR_INPUT` `QR_MASK` `QR_SCALE` `C128_MODE` `EAN13_DATA` `UPCA_DATA` `I2OF5_DATA` `MSI_DATA` `MSI_CHECK` `POSTAL_DATA` `DM_MODULE` `DM_QUALITY` `MPDF_MODE` `B4_INTERP` `BB_MODE` |
| 版式与文档块 | `FB_ALIGN` `FB_LINES` `MM` `MN` `MD` `PQ` |
| 照片重建 | `NO_QUAD` `ASPECT` `NO_OCR` `OCR_DROPPED` `HRI_MERGED` `MODULE_TOO_SMALL` `VISION_USED` `VISION_FAILED` `VISION_BARCODE_HINT` `BARCODE_UNVERIFIED` |
| 安全提示 | `CI_CJK`（提醒换字体名）、`MODULE_TOO_SMALL`（条会被读成别的制式） |

---

## 8. 环境变量

| 变量 | 作用 |
|---|---|
| `ZEBRA_VISION_URL` | 视觉大模型端点（OpenAI 兼容，写到 `/v1` 即可） |
| `ZEBRA_VISION_KEY` | 该端点的 key（只在一次请求头里出现，不回显/不落盘） |
| `ZEBRA_VISION_MODEL` | 模型名（省略时默认 `qwen2.5-vl-7b-instruct`，多数端点必须显式给） |
| `ZEBRA_OCR_MODELS` | PP-OCRv4 三个权重所在目录（或放 `data/ocr/`） |

---

## 9. 明确不支持

`^RF`/`^RW`/`^RU`（RFID 读写）、`^GF`（位图栅格流）、`~DG`/`~DY`（图形下载，只做知识查询）、`^DF`（存储格式调用）、`^HC`（主机查询）、SNMP 发现、队列/耗材监控、并发调度、HTTP 远程服务。

照片重建另外不转：线条、logo、图标、特殊字体（统一 `^A0` / `E:SIMSUN.TTF`）。

---

## 10. 一页速查

```text
# 最小可用标签
generate_zpl({"dpi":203,"width_mm":60,"height_mm":40,"fields":[
  {"type":"text","data":"NANJING","x_mm":40,"y_mm":29,"size_mm":5.4},
  {"type":"barcode","symbology":"code128","data":"ABC123","x_mm":3.8,"y_mm":2.6,
   "height_mm":9.4,"module_width":3},
  {"type":"barcode","symbology":"qrcode","data":"NGSU","x_mm":47,"y_mm":3,"scale":3}]})
# → {zpl, issues[], preview_file, ascii_preview}

# 打出去
send_zpl(zpl, "tcp", host="<打印机IP>", port=9100)     # 空回显 = 无语法错
printer_status(host="<打印机IP>")                        # ~HI/~HS，0000 = READY

# 表格批量
print_xlsx("D:/名单.xlsx", name_column="姓名", id_column="员工号", dry_run=True)

# 照片反推
zpl_from_image("D:/标签.png", width_mm=60, height_mm=40, frame="auto", dry_run=True)
```

三条纪律：

1. `issues` 里有 `error` 不打印；`warning` 要看（多数是「能打但可能扫不出」）。
2. 文本改了 `data` 就重发，不要手工改 ZPL——`^FD` 的转义与 `^CI` 是联动的。
3. 一次多张就拼一个流（`print_xlsx` 已这么做），别逐张调用。
---

## 11. 作为 agent 的调用手册

§2 是接口规格（谁、吃什么、吐什么）。这一节是**使用视角**：一个 agent 或写 agent 的人，实际怎么把它用起来。

### 11.1 注册（一次性，进程由客户端拉起）

MCP 是 stdio 进程，客户端负责启动，所以先登记启动命令。

```bash
# Hermes
hermes config set mcp_servers.zebra-printer '{"command":"<repo>/.venv/Scripts/python.exe","args":["-m","zebra_mcp.server"],"cwd":"<repo>","connect_timeout":30.0,"enabled":true}'
hermes mcp test zebra-printer        # → Connected, 8 tools discovered
```

```json
// Claude Desktop / 其它 stdio 客户端
{"mcpServers": {"zebra-printer": {
  "command": "<repo>/.venv/Scripts/python.exe",
  "args": ["-m", "zebra_mcp.server"],
  "cwd": "<repo>",
  "env": {"ZEBRA_VISION_URL": "http://<端点>/v1",
          "ZEBRA_VISION_KEY": "<key>",
          "ZEBRA_VISION_MODEL": "qwen2.5-vl-7b-instruct"}}}}
```

`env` 只有用视觉大模型认字时才需要；不给就走本地 PP-OCRv4。JSON 里用正斜杠路径（`\w` 这类反斜杠会被转义吃掉）。

不想装 MCP 客户端也能验证连通：`python scripts/mcp_probe.py` 走裸 JSON-RPC 列出工具。

### 11.2 注册后 agent 看到什么

`tools/list` 返回 9 个工具，名称按客户端规范加前缀（Hermes 里是 `mcp__zebra_printer__<tool>`）。参数 schema 由函数签名自动生成，模型据此填参——**它不需要知道任何一条 ZPL 命令**。

### 11.3 三种调用层次

**① 自然语言**（最常见，用户说人话，agent 自己选工具）

| 你想做 | 该说清的事实 | 落到哪个工具 |
|---|---|---|
| 出一张标签 | 尺寸、dpi、要打的内容、打到哪台机器 | `generate_zpl` → `send_zpl` |
| 批量出 | xlsx 路径、两列的定位、版式尺寸 | `print_xlsx`（一个流发完） |
| 复刻旧标签 | 照片路径 + **物理尺寸**（像素→点唯一比例尺） | `zpl_from_image` |
| 排查打不出 | 机器 IP | `printer_status` → `send_zpl(dry_run)` |
| 问某条码能不能打 | 制式名 | `zpl_knowledge(mode="barcodes")` |

**② 直接工具调用**（agent 内部、或你写代码时）

参数就是一个 label spec（§3/§4）。下面这条是真实跑出来的：

```json
{"label_spec": {"dpi": 203, "width_mm": 60, "height_mm": 40, "fields": [
  {"type":"text","data":"NANJING","x_mm":40,"y_mm":29,"size_mm":5.4},
  {"type":"barcode","symbology":"code128","data":"ABC123","x_mm":3.8,"y_mm":2.6,
   "height_mm":9.4,"module_width":3},
  {"type":"barcode","symbology":"qrcode","data":"NGSU","x_mm":47,"y_mm":3,"scale":3}]}}
```

```json
{"zpl": "^XA^PW480^LL320^FO320,232^A0N,43^FDNANJING^FS^FO30,21^BY3,3,10^BCN,75^FDABC123^FS^FO376,24^BQN,,3^FDQA,NGSU^FS^XZ",
 "issues": [], "preview_file": "…\\label_preview_34572.zpl", "ascii_preview": "+---…"}
```

**③ 裸 JSON-RPC**（自己写 client，不装 MCP SDK）

```text
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"me","version":"1"}}}
{"jsonrpc":"2.0","method":"notifications/initialized"}
{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"generate_zpl","arguments":{"label_spec":{…}}}}
```

纪律：一次一请求、每次读一行响应；`initialize` 之后必须先发 `notifications/initialized`，否则部分实现会拒后续调用。现成实现见 `scripts/mcp_probe.py`（61 行，纯标准库）。

### 11.4 四步循环（每一层都有停止判据）

```text
generate_zpl(spec)              → issues 里有 error：改 spec，不要发
render_zpl(zpl)                 → PNG：看重叠、越界、文本溢出（不用等纸）
send_zpl(zpl, dry_run=true)     → 落盘 .zpl + 预览，人/agent 看一眼版式
printer_status(host)            → identification 对得上机型 dpi 吗，status 是 0000 吗
send_zpl(zpl, host=…)           → 空回显 = 没语法错；有回显 = 打印机报了错，原样回读
```

| 观察到 | 含义 | 动作 |
|---|---|---|
| `issues` 有 `error` | 语法/范围错，或制式不支持 | 改 spec，别打 |
| 只有 `warning` | 能打，但可能扫不出（模块宽、静区、字体） | 打一张，扫一次 |
| `render_zpl` 的 `warnings` 说文本比 `^FB` 宽 | 打出来会被裁掉或压到邻居 | 改字号/块宽/文案，再渲染 |
| `render_zpl` 的 `approximated` 里有你的制式 | 预览没画它，别据此判断那个符号的位置 | 用 §5 的参数表核对，实机打一张 |
| `send_zpl` 回显为空 | 打印机接受了整段 ZPL | 继续；纸面仍需扫码验收 |
| 回显里有 `^XA` 相关错误 | 该标签块被拒 | 把回显原文交回来，按提示改 |
| `printer_status` 非 `0000` | 缺纸/开盖/暂停/过热 | 先处理硬件，别怀疑 ZPL |

### 11.5 提示词写法（决定成败的一半）

| 别这么说 | 这么说 |
|---|---|
| "生成 ZPL 打印" | "60×40 mm、203 dpi 的标签：左上 Code128 内容 ABC123，右上二维码指向 https://… ，底部 NANJING，先 dry_run" |
| "循环打 N 次" | "这个 xlsx，一列姓名一列工号打 128 码、居中" → 走 `print_xlsx`，整批一个流 |
| "照这个样式做一张" | 给照片 + **物理尺寸** + 要不要用视觉大模型认字 |
| "支持 DataMatrix 吗" | "调 `zpl_knowledge(mode='barcodes')` 查制式表，给出命令与参数位序" |

物理事实（尺寸、dpi、机器地址、份数）必须由人给；这些猜错不会报错，只会打出比例错误的标签。

### 11.6 五个默认值与边界坑

1. **`send_zpl` 的 `dry_run` 默认 false**——agent 想"先看看"，必须显式传 `dry_run=true`，否则一调就出纸。
2. **Windows/USB 队列的 `job_id` 只证明入队成功**，不证明纸出来了；要确凿证据只用 `tcp` 并看回显。
3. **stdio 进程跟着客户端活**：客户端退出、会话结束，进程就没了；没有重试、没有队列。常驻需求见 §9。
4. **一次多张拼一个流**（`print_xlsx` 已这么做）。逐张调用 = 每张都等一次回显，500 张差一个数量级。
5. **视觉大模型 key 不要写进对话**：放客户端 MCP 配置的 `env`；工具本身不会回显它，错误信息也会脱敏。

### 11.7 不经 agent：命令行孪生

```bash
python scripts/xlsx_labels.py 名单.xlsx --name 姓名 --id 员工号 --dry-run
python scripts/xlsx_labels.py 名单.xlsx --name 姓名 --id 员工号 --execute --host <打印机IP>
```

表格批量有 CLI 孪生（默认 dry-run，`--execute` 才发送）。生成单张标签没有等价 CLI，用 JSON-RPC 探针即可。

