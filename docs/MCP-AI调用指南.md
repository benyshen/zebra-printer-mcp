# Zebra 打印 MCP · AI 调用指南

> **一句话**：让模型只说"这张标签要什么"，ZPL 由工具生成、校验、送出——模型不需要背 ZPL 语法。
>
> 版本 0.1.1 ｜ 仓库 <https://github.com/benyshen/zebra-printer-mcp> ｜ 实测机型 ZR668-200dpi（固件 V86.20.21Z）
>
> 同目录另有一篇 [`MCP-技术交流文档.md`](MCP-技术交流文档.md)：讲协议、讲 ZPL 细节、讲踩过的坑。**这一篇讲选型**——它凭什么适合 AI 调用、怎么用、什么活该交给它、什么活不该。

---

## 1. 它是什么，不是什么

| | 说明 |
|---|---|
| **是** | 一个本地 **stdio MCP server**（不是 HTTP 服务、不是云端 API），把"生成 ZPL / 校验 ZPL / 查手册 / 列打印机 / 发送 / 查状态 / 按表格批量打"七件事封成七个工具 |
| **是** | 知识来源固定：ZPL II 编程手册（`zpl-zbi2-pg-en`）的 253 条命令目录、27 种条码制式的 Format 行页码——答案可回溯到手册页，不是模型记忆 |
| **不是** | 打印中间件：没有队列管理、没有重试与审计、没有多机调度 |
| **不是** | 标签设计器：不做版式拖拽、不做图像半色调、不做套印对齐 |
| **不是** | 网络服务：无端口、无鉴权面，跟着客户端进程活（stdio 生命周期） |

---

## 2. 为什么值得给 AI 一个 MCP，而不是让它直接写 ZPL

先说结论，再说代价。

### 2.1 模型直写 ZPL 的真实故障模式

ZPL 是**无校验的文本协议**：语法错了打印机会照样吃，版式错了会打出一张很正式的错标签。让模型凭记忆写 ZPL，典型失效点（本项目复检时**全部踩到过**）：

| 失效点 | 后果 | 是否静默 |
|---|---|---|
| `^B9`/`^B0`/`^BR`/`^BF`/`^B4` 的参数**字母位序**记错 | 条码高度跑到别的位、解释行错位 | 静默，照打 |
| QR 的 `^FD` 漏掉强制前缀 `<纠错级><输入模式>,` | 数据前两字符被当开关吃掉：`https://…` → 扫出来是 `ps://…` | 静默，照打 |
| mm→dots 手算（203dpi = 8 dots/mm） | 尺寸差 1–2 mm，或坐标越界被截断 | 静默 |
| 中文没配 `^CI28` + UTF-8 + `^A@` | 打出问号或乱码 | 静默 |
| 忘了 `^XA/^XZ` 配对 | 打印机等后续字节，作业卡住 | 半静默 |

这些错**都不是"模型不够聪明"**，而是要求模型在每次调用时背负一张 27 行 × 5 个字母位序的表格。工具的意义就是把这张表搬到服务端。

### 2.2 走 MCP 之后省下的是什么

| 维度 | 模型直写 ZPL | 本 MCP |
|---|---|---|
| 模型需要的知识 | 27 制式位序、`^FD` 前缀、`^CI` 编码、单位换算 | 一个 JSON spec（`type/data/x_mm/y_mm/size_mm`…） |
| 出错时机 | 打出来才发现（要人工扫码/量尺） | 发射前返回 `issues[]`，带 `code`/`field`/手册页码 |
| 出错方式 | 静默错标签 | 可自纠错的返回（模型读到 `QR_FD_PREFIX` 就知道补前缀） |
| 复现性 | 每次生成文本不同 | **同一 spec → 同一字节**（可 diff、可进回归） |
| 上下文成本 | 每次要把手册片段塞进 prompt（几千 token） | `tools/list` 只暴露 8 个 schema |
| 纸张成本 | 打废一张 = 一张纸 + 一次扫码确认 | 先 `validate` + `dry_run`，再落纸 |
| 换模型 | 换模型 = 重新调 prompt | 换模型 = 不动调用方（工具契约不变） |

**诚实的代价**：单条标签的 spec **不一定比 ZPL 短**。实测一个 5 字段标签（中文标题 + Code128 + QR + 页脚 + 边框）：spec JSON **590 字符**，生成的 ZPL **217 字符**。所以这里省的不是"每次调用的 token"，省的是**模型要知道的东西**和**错了以后返工的成本**。真要压 token，把 spec 做成参数化模板（只变 `data` 字段）才划算。

### 2.3 三条额外的工程收益

1. **手册可溯源**：`zpl_knowledge(mode="command", query="^BR")` 直接给语法、默认值、页码——模型"查"而不是"猜"，答案能被人核对。
2. **确定性可审计**：同 spec 同字节 → 可以放进 git、可以写断言、可以对比新旧模板差异（本项目 29 组断言就是这么锁的）。
3. **无网络暴露面**：stdio 本地子进程，不监听端口、不需要 token、不出网。企业内网里这是最容易过审的形态。

---

## 3. 使用方法

### 3.1 注册（三个客户端任选）

```bash
# Hermes（本项目实测路径）
hermes config set mcp_servers.zebra-printer.command 'E:/workspace/zebra-printer-mcp/.venv/Scripts/python.exe'
hermes config set mcp_servers.zebra-printer.args '["-m","zebra_mcp.server"]'
hermes config set mcp_servers.zebra-printer.cwd 'E:/workspace/zebra-printer-mcp'
hermes mcp test zebra-printer        # 期望：Connected / Tools discovered: 6
```

Claude Desktop / 其它 stdio 客户端用同构的 `{command, args, env}` 字段即可（详见技术文档 §7）。
Hermes 写 config 时 JSON 路径用**正斜杠**，且别直接手改 `config.yaml`。

### 3.2 四步闭环（一次对话就能跑完）

```text
① generate_zpl(label_spec)   → { zpl, issues[], preview_file, ascii_preview }
② validate_zpl(zpl)          → { issues[], ok }         # 想手改 ZPL 时才需要
③ send_zpl(zpl, dry_run=true)→ 不落纸，回预览文件        # 不确定版式时先看
④ send_zpl(zpl, transport_kind="tcp", host="<打印机IP>")
```

最小可用 spec（坐标单位 mm，dpi 203 自动换算成 dots）：

```json
{
  "dpi": 203, "width_mm": 70, "height_mm": 40, "quantity": 1,
  "barcode_defaults": {"module_width": 3},
  "fields": [
    {"type": "text", "data": "条码AI一线 SKU-2026",
     "x_mm": 2, "y_mm": 2, "size_mm": 3.5, "font_file": "E:SIMSUN.TTF"},
    {"type": "barcode", "symbology": "code128", "data": "ABC12345",
     "x_mm": 6, "y_mm": 9, "height_mm": 10, "interpret": true},
    {"type": "barcode", "symbology": "qrcode", "data": "https://example.com/p/1",
     "x_mm": 53, "y_mm": 8, "scale": 4},
    {"type": "box", "x_mm": 1, "y_mm": 1, "width_mm": 68, "height_mm": 38}
  ]
}
```

> 这段示例**验证过**：`generate_zpl` → 194 字节 ZPL → Labelary 8dpmm 渲染 → zxing 解码，同时读回
> Code128 `ABC12345` 和 QR `https://example.com/p/1`。两个坐标不是随手填的，见下面的静区说明。

模型只需要区分三件事：**哪个字段**（`type`）、**放在哪**（`x_mm/y_mm`）、**内容是什么**（`data`）。条码的纠错级、`^FH` 转义、`^CI28` 编码、`^BY` 默认值全部由服务端补。

**坐标不是随便填的——静区（quiet zone）比生成逻辑更容易毁掉一张标签**：一维码左右各需要 ≥10 倍模块宽的空白。
`module_width=3`（3 dots ≈ 0.375 mm）就要 ≥3.75 mm 的净空。实测把 Code128 放在 `x_mm=2`（离边框 1 mm）时，
渲染出来的条码**格式完全正确但扫不出**；挪到 `x_mm=6` 立刻可读。`^BY` 默认模块宽是 **2 dots = 0.25 mm**（临界值，
零售标签常用），要更稳就给 3。

### 3.3 传输怎么选（决策树）

```text
打印机能 ping 通、9100 端口开着？
├─ 是 → transport_kind="tcp"      ← 首选。唯一能拿到打印机回显的通道
└─ 否 → 打印机插在这台 Windows 上？
        ├─ 是 → "windows"（winspool RAW，驱动不参与渲染，job_id 只证明入队）
        └─ 否 → "usb"（同样走队列）或先把标签存成 .zpl 拷到 U 盘
不确定版式 / 只想看效果？
└─ dry_run=true 或 send_zpl(transport_kind="preview")  ← 不落纸
```

| transport | 依赖 | 能验证到什么程度 |
|---|---|---|
| `tcp` | 网络 + 9100 | **端到端**：字节送达 + 打印机错误回显（空回显 = 无语法错） |
| `windows` / `usb` | Windows 打印队列 | 只到"入队成功"。虚拟打印机会**假成功** |
| `preview` | 无 | 本地 `.zpl.preview` + ASCII 近似版式（不是像素级） |

### 3.4 排错：看 `issues[]` 的 `code`

`generate_zpl` / `validate_zpl` 都返回结构化问题，模型可以直接读着改：

| code | 含义 | 处理 |
|---|---|---|
| `QR_FD_PREFIX` | QR 的 `^FD` 缺 `<纠错级><输入模式>,` | 用 `generate_zpl`，别手拼 |
| `UNKNOWN_CMD` | 命令不在 253 条目录内 | 拼写错，或用了本机型不支持的扩展 |
| `CI_CJK` | 有非 ASCII 但没配 `^CI28`/UTF-8 | 加 `font_file` 或让工具接管编码 |
| `EAN13_DATA` / `UPCA_DATA` | 数据位数/校验位不对 | 补足 12/11 位，或让打印机算校验 |
| `BARCODE_UNKNOWN` | `symbology` 名字不在 27 种里 | 用 `zpl_knowledge(mode="barcodes")` 查名字 |
| `POS` / `ORIGIN` / `PW` / `LL` | 坐标或标签尺寸超出打印区 | 缩小尺寸，或改 `print_width_dots`/`label_length_dots` |
| `FONT_FILE` / `TEXT_SIZE` | 字体文件名或字号非法 | 用 `E:SIMSUN.TTF` 这类打印机内文件名；字号 10–32000 dots |

全部 44 个 code（`QR_*`、`DM_*`、`FB_*`、`C128_MODE`、`I2OF5_DATA`、`HEX_NEEDED`…）都在 `builder.py` 里，按 `<制式>_<问题>` 命名，可直接当判断依据。

现象侧对照：
- **打得出来但扫不出** → 先查 QR 前缀与纠错级；二维码没问题就查**静区**（离边框/相邻元素太近）和 `module_width`。
- **二维码变小/变形** → `scale`(c) 是倍率，`^BY` 的模块宽也会影响一维码宽度，两者一起决定占位。
- **中文是问号** → 字体文件名（`E:SIMSUN.TTF`，须是打印机内的文件）或 `^CI28`。

> 静区、模块宽、纠错级这类"版式物理约束"工具不会替你检查——它们不违反 ZPL 语法，却直接决定能不能扫。
> 唯一可靠的验收是**打一张扫一张**（或用本文的渲染 + 解码法）。

### 3.5 批量：给一个 `.xlsx`，一行一张标签

`print_xlsx` 把"表格 → 标签"整段包了：读第一张（或指定）工作表，取两列，一行一张标签。姓名用
`^FB …,C` 由**打印机按真实字体度量**居中；工号打成 Code 128，宽度按 `11×(n+2)+13` 个模块算出来再居中对齐
（`^BC` 没有对齐参数，只能算）。

```text
print_xlsx(xlsx_path="D:/名单.xlsx",
           name_column="姓名",     # 也可以 "A" 或 1
           id_column="员工号",     # 也可以 "B" 或 2
           width_mm=50, height_mm=30,
           name_size_mm=4, barcode_height_mm=10, module_width=3,
           dry_run=False, host="<打印机IP>")
```

返回 `{labels, rows_read, bytes, issues[], zpl_file, first_label, sent}`。`rows_read` 与 `labels` 的差
就是被跳过的行（工号为空不打）；`issues` 里每条都带 `label` 序号，能定位到第几张。

三条要点：

- **一次连接发整批**：所有标签拼成**一个 ZPL 流**（每张一个 `^XA…^XZ` 块），打印机回显只读一次。
  实测 5 张 **1.03 s**；逐张调用就是 5×1 s。表里有 500 行时这是 8 分钟和 1 分钟的差别。
- **中文列要有字体**：`name_size_mm` 决定 `^A@` 的高度，默认 `E:SIMSUN.TTF`。机型没有该字体时
  `issues` 会报 `CI_CJK`——不是错误，是提醒你换字体名。
- **只吃 `.xlsx`/`.xlsm`**：用标准库解 zip+XML，不带 openpyxl 依赖。`.xls` 请先在 Excel 里另存。

不想让模型经手时，命令行同款：

```bash
python scripts/xlsx_labels.py 名单.xlsx --name 姓名 --id 员工号 --dry-run
python scripts/xlsx_labels.py 名单.xlsx --name 姓名 --id 员工号 --execute --host <打印机IP>
```

### 3.6 照片：给一张现有标签的图，反出 ZPL

`zpl_from_image` 是**重建**，不是设计：解码 + OCR + 像素映射，全在本仓库（`photo.py` / `ocr.py`）。

```text
zpl_from_image(image_path="D:/标签.png", width_mm=60, height_mm=40,
               frame="auto",          # 标签在画面里带背景 -> auto；已裁好 -> full
               dry_run=True)          # 先看 elements，确认无误再 dry_run=False + host 直发
```

返回 `{ok, zpl, elements[], decoded[], warnings[], validation, symbol_count, scale}`。

四步流水线与各自的坑：

| 步 | 做什么 | 什么时候会失手 |
|---|---|---|
| 找标签 | Otsu + 轮廓 + 四边形，双极性各试；透视校正 | 背景和标签同色、倾斜 >45° → 退回整图并给 `NO_QUAD` |
| 解码 | zxing-cpp 读全部一维码/二维码 | 糊、反光、模块 <2 px → 拿不到，`symbol_count: 0`（**不会编造内容**） |
| 读字 | PP-OCRv4 det+rec | 字高 <12 px 开始出错（154 px 宽的 `NANJING` 读成 `NAN-ING`，置信度 0.87） |
| 映射 | `dot = px × 标签宽 / 照片宽`；一维码反推模块宽、二维码反推放大倍数、文字 `size ≈ 框高/0.775` | `width_mm/height_mm` 给错 = 整体缩放，`ASPECT` 警告只在差得离谱时响 |

两个已修的真缺陷，值得知道它们为什么必要：

- **HRI 必须按位置合并**：一维码下面那行字，OCR 十有八九错一个字符（`ABC123`→`ABC1Z3`）。按字面比对 → 判为"另一段文字"→ 标签上这行打两遍（一遍 `^BC …,Y`，一遍当文本）。
- **`^BY` 必须能逐字段发**：一张标签上 128 码和二维码的模块宽不同，`^BY` 是打印机级设置，只在标签开头发一次会把两者绑死。现在字段带 `module_width` 就就地发 `^BY`。

诚实边界：字体认不出（统一 `^A0` / `E:SIMSUN.TTF`）、**线条/logo/图片不转**（不生成 `~DG`/`^GF`）、小字 OCR 会错。所以流程是"生成 → 人看一眼 `elements` → 再打"，不是"拍完直接打"。

---

## 4. 能实现的功能模块

### 4.1 八个工具（对外能力面）

| 工具 | 入参 | 返回 | 实测耗时 | 依赖 |
|---|---|---|---|---|
| `zpl_knowledge` | `mode`（command/search/barcodes/sgd/status/list）、`query` | 语法+默认值+页码 / 制式规则 / 状态码表 | <1 ms | 无 |
| `generate_zpl` | `label_spec` | `{zpl, issues[], preview_file, ascii_preview}` | ≈0.1 ms | 无 |
| `validate_zpl` | `zpl` | `{issues[], ok}`（四层：`^FD` 抹除 → 253 命令目录 → QR 前缀 → `^XA/^XZ` 配对） | ≈0.15 ms | 无 |
| `list_printers` | — | 队列名/端口/驱动/状态 | 0.3–1 s | Windows PowerShell |
| `send_zpl` | `zpl`、`transport_kind`(tcp/windows/usb/preview)、`host`/`printer`/`port`、`dry_run` | 发送结果 + 回显 | ≈1.0 s（tcp） | 网络或队列 |
| `printer_status` | `host`、`port`、`sgd`、`timeout` | `{identification(~HI), status(~HS 第3行), sgd{}, native_status}` | ≈3.9 s | 网络 |
| `print_xlsx` | `xlsx_path`、`name_column`/`id_column`（字母/序号/表头名）、尺寸与 `dry_run` | `{labels, rows_read, bytes, issues[], zpl_file, first_label, sent}` | 5 张 ≈1.0 s（单流） | 读 xlsx 用标准库，发送走 TCP |
| `zpl_from_image` | `image_path`、`width_mm`/`height_mm`（比例尺）、`frame`(auto/label/full)、`use_ocr`、`dry_run`/`host` | `{ok, zpl, elements[], decoded[], warnings[], validation, symbol_count}` | 首次 0.45 s（装模型），之后 ≈0.14 s | cv2 + zxing-cpp + onnxruntime（`[photo]` extra） |

### 4.2 内部模块（改代码时才关心）

| 模块 | 行数 | 职责 |
|---|---|---|
| `builder.py` | 713 | spec→ZPL（含 27 制式 `BARCODE_SPECS`、图形、单位换算、范围校验、`validate_zpl`） |
| `knowledge.py` | 337 | 253 命令目录、69 条命令页、27 制式规则、27 条 SGD、13 个状态码 |
| `transport.py` | 257 | 编码仲裁（`^CI28`+UTF-8）、TCP、winspool RAW、ASCII 预览 |
| `server.py` | 396 | 八个工具、SGD↔原生（`~HI`/`~HS`）状态回退 |
| `catalog.py` | 15 | 把手册目录注入校验器 |
| `photo.py` | 345 | 照片→spec：找标签/透视校正、解码、几何映射、`^BY` 反推 |
| `ocr.py` | 242 | PP-OCRv4 det+rec（ONNX），DB 后处理 + CTC 解码 |
| `xlsx_batch.py` | 185 | 标准库读 xlsx（zip+XML）、列解析、行→spec、Code128 宽度算居中、多标签单流 |
| `tests/test_builder.py` | 336 | 29 组断言（位序、QR 前缀、`^A@`、编码、预览、知识库覆盖、xlsx 批量、逐轴坐标、照片→ZPL 几何与回环） |

### 4.3 覆盖范围（能做的）

- **条码 27 种**：Code128/39/93/11、I2of5、Std2of5、Codabar、MSI、Plessey、Logmars、EAN13/EAN8/UPCA/UPCE/UPCExt、GS1 DataBar、Code49、Codablock、PDF417/MicroPDF417、DataMatrix、QR、Aztec、MaxiCode、Planet/Postnet/USPS IM。
- **图形**：`box`（`^GB`）、`circle`（`^GC`）、`reverse`（`^FR`）、`image`（`^XGR` 引用已下载的 `.GRF`）。
- **版式**：`^FO`/`^FT`、`^FB` 多行块（自动折行/对齐）、`^FH` hex 转义、字符编码、镜像/反白、`^PR` 速度、`^MD` 浓度、`^MN`/`^MM` 介质。
- **中文**：内置 `E:SIMSUN.TTF` / `E:HANS.TTF` 配方实测可打（`^CI28` + UTF-8 + `^A@`）。
- **状态**：SGD 优先，Fusion 老固件自动回退 `~HI`/`~HS`，`0000 = PRINTER READY`。

### 4.4 覆盖范围（做不到的，别指望）

`^RF`/`^RW`/`^RU`（RFID 读写）、`^GF`（位图栅格流）、`^DF`（存储格式调用）、`^HC`（主机查询）、`~DY`/`~DG` 只做到知识查询、**没有**下载工具、无 SNMP 发现、无队列监控。

---

## 5. 适宜场景

| 场景 | 为什么合适 | 一句话形态 |
|---|---|---|
| **Agent / 聊天里随口出标签** | 模型只需填 spec，不需要 ZPL 知识；`issues[]` 让它自己改对 | "打一张 70×40 的，标题是这批货号，右下角二维码指向这条链接" |
| **参数化小批量** | 模板固定、只变 `data`，spec 短、输出确定 | 一天几十张 SKU/批次/序列号标签 |
| **表格批量**（名单/工号/资产编号） | `print_xlsx` 一次读表出整批，单连接单回显 | 给一个 xlsx：一列姓名、一列工号打 128 码，居中 |
| **老标签数字化**（只有纸质样张，没有源文件） | `zpl_from_image` 解码 + OCR + 几何映射，几十秒出可编辑的 spec/ZPL | 拍一张旧标签 → 出 ZPL → 改文字重打 |
| **现场调试**（手边没有 BarTender，只有一台网口打印机） | TCP 直发 + 回显 + `printer_status`，不装驱动 | "打印机打不出来，先看状态字" |
| **中文标签** | 编码与字体配方已封装，实测过 | 中文品名 + 条码 + 二维码一张出 |
| **标签模板治理** | 同 spec 同字节 → 可 diff、可回归、可 code review | 把标签当代码管，改模板跑 29 组断言 |
| **教学 / 技术交流** | `zpl_knowledge` 让"手册"变成可问的对象；错误码能把坑讲清楚 | 现场演示"错一位参数会发生什么" |
| **给业务系统加个 AI 出口**（MES/ERP 助手） | 业务侧只给结构化字段，打印细节不外泄 | 拣货完成 → 自动出箱标 |
| **多模型混用** | 工具契约稳定，换模型不改调用方 | Claude/DeepSeek/GPT 共用一套 |

**共同点**：标签**种类多、单量小、需要人能看懂为什么**。这正是"让模型填字段 + 服务端保证语法"最划算的区间。

---

## 6. 不适宜场景（以及该用什么）

| 场景 | 为什么不合适 | 换什么 |
|---|---|---|
| **高速产线连续打印**（>1 张/秒） | `send_zpl(tcp)` 实测 **≈1.0 s/张**（含等打印机回显），串行且无并发调度 | Zebra **PrintConnect** / **SDS**，或自建 socket 长连接流水线（本项目代码可复用 `transport.send_tcp`） |
| **无人值守常驻服务** | stdio 进程跟着客户端活；无重试、无队列、无失败落盘 | 包成 Windows 服务或 HTTP 网关（见技术文档 §10 路线图），或直接用打印中间件 |
| **复杂版式 / 照片 / 半色调** | 未实现 `^GF` 位图流，`image` 只能引用打印机里已有的 `.GRF` | **BarTender** / **Zebra Designer** 排版，导出 `.zpl` 再交给 `send_zpl` |
| **照片转 ZPL 想要"一模一样"**（logo、图标、特殊字体、网点） | 只还原码内容/文字/坐标；图与线不转，字体统一为 `^A0`/`E:SIMSUN.TTF` | 设计器重排；`zpl_from_image` 只当"把老标签的字段抠出来"的起点 |
| **拍完直接批量打**（一次几百张照片） | 每张都要人看一眼 `elements`：小字 OCR 会错一个字，错字打出去就是废标签 | 人工确认一版 spec 后转成模板，后续走 `generate_zpl`/`print_xlsx` |
| **RFID 编码**（写 EPC/标签锁定） | `^RF`/`^RW`/`^RU` 未封装 | Zebra **123RFID** / SDK |
| **非 ZPL 机型**（EPL/CPCL 老机、部分桌面机） | 只发 ZPL II | 对应机型语言，或换机型 |
| **多打印机集群 / 作业管理** | 无并发调度、无队列监控、无耗材计数 | **Zebra Printer Profile Manager** / SDS / PrintConnect |
| **合规审计留痕**（谁在什么时候打了什么） | 不落盘、不签名，只能靠调用方记录 | 中间件 + 打印日志系统；本工具可只做"生成+校验"环节 |
| **0.1 mm 级套印对齐 / 预印纸套打** | 预览是 ASCII 近似，不是像素级；无套印校准流程 | 设计器打样 + 实机调 `^LT`/`^LS`，再固化 spec |
| **跨网段/云侧远程调用** | stdio 本地进程设计，不是 HTTP 服务 | 自建 HTTP 包装（同一个 `builder`/`transport` 可直接复用） |

> 判断口径很简单：**你要的是"把一张标签正确地打出来"，还是"运营一条打印产线"**。前者用它，后者用它上游/下游的专业件。

---

## 7. 性能与规模（实测，非估算）

| 动作 | 实测 | 说明 |
|---|---|---|
| `generate_zpl`（5 字段） | ≈0.1 ms | 纯字符串拼装 |
| `validate_zpl` | ≈0.15 ms | 253 条目录逐 token 比对 |
| `send_zpl(tcp)` | **≈1.0 s** | 大头是等打印机回显（错误文本）的空闲窗口，不是网络慢 |
| `printer_status` | **≈3.9 s** | 4 条 SGD（本机型回 `?`）+ `~HI`/`~HS` 两条原生；复检时从 13.4 s 优化到 3.9 s |
| `print_xlsx`（5 张） | **≈1.0 s** | 整批一个流；500 张同样是"1 次连接 + 1 次回显"，瓶颈变成打印速度而非调用次数 |
| `list_printers` | 0.3–1 s | PowerShell 枚举，进程启动占大头 |
| `zpl_from_image` | **0.14 s**（热）/ 0.45 s（冷） | 480×320 样张：解码 + 2 行 OCR + 几何映射；冷启是 ONNX 模型加载 |

**吞吐结论**：串行 ≈1 张/秒，且**没有内建并发**。要更快就绕开"等回显"（把回显窗口调小或做长连接），这属于改造而不是配置。

---

## 8. 与其它方案的位置

| 方案 | 强项 | 弱项 | 与本 MCP 的关系 |
|---|---|---|---|
| 模型直写 ZPL | 零依赖、任意版式 | 静默错标签、每次要喂手册 | 被替代 |
| Labelary（在线渲染） | 像素级预览 | 只是渲染，不发送、不校验语义 | 互补：本项目用它做过 QR 解码验证 |
| BarTender / Designer | 版式设计、图像、套印 | 重、要授权、不适合 AI 动态拼装 | 上游：设计好导出 ZPL，再交给 `send_zpl` |
| PrintConnect / SDS | 产线级队列、并发、监控 | 部署重、按产线计费 | 下游：真上产线用它 |
| **本 MCP** | AI 原生、语义入参、手册可溯源、零网络暴露 | 单张约 1 s、无队列/审计/RFID/图像流 | 适合"AI 到标签"这一段 |

---

## 9. 一页速查

**该用**：标签种类多、单量小、要人看得懂、要能回归、要中文、要现场零依赖。

**别用**：产线高速连续、无人值守、复杂版式、RFID、多机集群、合规留痕。

**三条纪律**：
1. 让模型**只填 spec**，别让它手拼 ZPL（`^BQ` 前缀、`^B9` 位序这类坑人眼也看不出）。
2. 落纸前先 `validate_zpl`，不确定版式就 `dry_run=true`。
3. 只有 `transport_kind="tcp"` 能告诉你"打印机真的收到了并且没报错"；队列通道的 `job_id` 只代表入队。

**最快的自检**：

```text
printer_status(host) → status.code == "0000"      # 打印机就绪
generate_zpl(spec)   → issues[] 为空               # spec 合规
send_zpl(zpl, dry_run=true) → 有 preview_file      # 版式可看
send_zpl(zpl, "tcp", host) → ok=true 且回显为空    # 真打成功
```

---

*本文与 0.1.1 代码同步（2026-10-22）；性能数字来自一台 ZR668-200dpi（局域网 9100）、Python 3.14、Windows 11 的实测。*
