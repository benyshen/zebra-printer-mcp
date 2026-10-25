"""zebra_mcp — MCP 外壳（stdio），引擎在 zpl-core。

本包只剩协议层：9 个工具的 schema 与参数校验、JSON-RPC 生命周期、结果序列化。
ZPL 生成、校验、渲染、照片反推、批量 Excel、传输全部来自 ``zpl_core``。

    pip install zebra-printer-mcp        # 带 zpl-core[render]
    pip install zebra-printer-mcp[photo] # 再加照片 -> ZPL（OCR 模型随 zpl-core 走）
"""
from __future__ import annotations

__version__ = "0.4.0"

# 引擎版本随 zpl-core 走，两个包必须能对上
try:
    import zpl_core

    ENGINE_VERSION = zpl_core.__version__
except ImportError:  # pragma: no cover - 依赖没装时给一句人话
    ENGINE_VERSION = "missing (pip install 'zpl-core[render]')"

__all__ = ["__version__", "ENGINE_VERSION"]
