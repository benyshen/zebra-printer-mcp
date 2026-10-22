"""Optional vision-LLM backend for photo -> ZPL.

The local path (zxing-cpp + PP-OCRv4) needs no network and no key. This module is
the alternative for machines where the OCR weights are not installed, or where
the text is stylised enough that a VLM beats PP-OCRv4. It speaks the OpenAI
chat-completions shape, so any compatible endpoint works: vLLM, ollama, LM Studio,
Qwen/DashScope, Moonshot, OpenAI, a local Strata box, ...

Rules this module keeps:
  * the key is used once, in a request header, and never written anywhere --
    not to logs, not to a returned dict, not to disk;
  * errors come back redacted, so a base64 image or a bearer token cannot leak
    into a traceback or a tool response;
  * the model is asked for normalised boxes (0..1) and nothing else, so the
    pixel -> dot mapping stays in photo.py where it is verifiable.
"""
from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request

__all__ = ["config", "available", "analyze"]

ENV_URL = "ZEBRA_VISION_URL"
ENV_KEY = "ZEBRA_VISION_KEY"
ENV_MODEL = "ZEBRA_VISION_MODEL"

SYSTEM = (
    "You read printed labels. Return JSON only, no prose, no markdown fences.\n"
    "Schema: {\"elements\":[{\"kind\":\"text\"|\"barcode\",\"text\":str,"
    "\"format\":str|null,\"box\":{\"x\":0..1,\"y\":0..1,\"w\":0..1,\"h\":0..1}}]}\n"
    "box is the element's bounding box as fractions of image width/height.\n"
    "kind=text: transcribe exactly as printed, keep dots/hyphens, do not translate.\n"
    "kind=barcode: text is the human-readable value; format is one of Code128, "
    "Code39, Code93, EAN13, EAN8, UPCA, ITF, Codabar, QRCode, DataMatrix, PDF417, "
    "Aztec; null if unknown.\n"
    "Do NOT report a barcode's bars as text. Do NOT invent elements. "
    "If the image has no label, return {\"elements\":[]}."
)


def config(base_url: str = "", api_key: str = "", model: str = "") -> dict:
    """Resolve the endpoint from arguments first, environment second.

    Returns {"ok": bool, "base_url", "api_key", "model", "error"?}. The key is in
    the dict only because the caller needs it for the header -- never serialize
    this dict into a tool response.
    """
    url = (base_url or os.environ.get(ENV_URL) or "").strip()
    key = (api_key or os.environ.get(ENV_KEY) or "").strip()
    mdl = (model or os.environ.get(ENV_MODEL) or "").strip()
    if not url:
        return {"ok": False, "error": f"no endpoint: pass llm_url or set {ENV_URL}",
                "base_url": "", "api_key": "", "model": ""}
    if not mdl:
        mdl = "qwen2.5-vl-7b-instruct"      # only a default; most endpoints need their own name
    if "/chat/completions" not in url:
        url = url.rstrip("/") + "/chat/completions"
    return {"ok": True, "base_url": url, "api_key": key, "model": mdl}


def available() -> dict:
    cfg = config()
    if not cfg["ok"]:
        return cfg
    return {"ok": True, "endpoint": cfg["base_url"], "model": cfg["model"],
            "key_set": bool(cfg["api_key"])}


def _redact(text: str, key: str = "") -> str:
    if key:
        text = text.replace(key, "***")
    return text[:400]


def _post(url: str, payload: dict, key: str, timeout: float) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _json_from(text: str) -> dict:
    """Models wrap JSON in fences or prose about 10% of the time; dig it out."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.split("```")[1] if "```" in t[3:] else t.strip("`")
        t = t[4:] if t.lower().startswith("json") else t
    try:
        return json.loads(t)
    except Exception:
        pass
    i, j = t.find("{"), t.rfind("}")
    if i >= 0 and j > i:
        return json.loads(t[i:j + 1])
    raise ValueError("model returned no JSON object")


def analyze(image_bgr, *, base_url: str = "", api_key: str = "", model: str = "",
            timeout: float = 60.0) -> dict:
    """Image -> {"ok", "elements":[...], "usage", "model"} or {"ok": False, "error"}.

    Elements carry normalised boxes and are NOT trusted for barcode content:
    a VLM happily misreads digits, so photo.py only uses its barcode entries
    when zxing decoded nothing, and marks them unverified.
    """
    import cv2

    cfg = config(base_url, api_key, model)
    if not cfg["ok"]:
        return {"ok": False, "error": cfg["error"]}
    ok, buf = cv2.imencode(".png", image_bgr)
    if not ok:
        return {"ok": False, "error": "cannot encode the image as PNG"}
    b64 = base64.b64encode(buf.tobytes()).decode("ascii")
    payload = {
        "model": cfg["model"],
        "temperature": 0,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": [
                {"type": "text", "text": "Transcribe this label."},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ]},
        ],
    }
    try:
        resp = _post(cfg["base_url"], payload, cfg["api_key"], timeout)
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:
            pass
        hint = {401: "check llm_key", 403: "key lacks access to this model",
                404: "check llm_url (must be .../v1/chat/completions)",
                413: "image too large", 429: "rate limited"}.get(exc.code, "endpoint error")
        return {"ok": False, "status": exc.code,
                "error": f"HTTP {exc.code}: {hint} -- {_redact(body, cfg['api_key'])}"}
    except urllib.error.URLError as exc:
        return {"ok": False, "error": f"cannot reach {cfg['base_url']}: {_redact(str(exc.reason))}"}
    except Exception as exc:
        return {"ok": False, "error": _redact(f"{type(exc).__name__}: {exc}", cfg["api_key"])}

    try:
        content = resp["choices"][0]["message"]["content"]
        if isinstance(content, list):       # some servers return parts
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        parsed = _json_from(content)
    except Exception as exc:
        return {"ok": False, "error": _redact(f"unexpected response shape: {type(exc).__name__}: {exc}",
                                             cfg["api_key"])}

    out = []
    for el in parsed.get("elements") or []:
        if not isinstance(el, dict):
            continue
        box = el.get("box") or {}
        try:
            b = [float(box.get("x", 0)), float(box.get("y", 0)),
                 float(box.get("w", 0)), float(box.get("h", 0))]
        except Exception:
            continue
        kind = "barcode" if str(el.get("kind", "")).lower().startswith("bar") else "text"
        text = str(el.get("text", "")).strip()
        if not text or b[2] <= 0 or b[3] <= 0:
            continue
        out.append({"kind": kind, "text": text,
                    "format": (el.get("format") or None) if kind == "barcode" else None,
                    "box": [max(0.0, min(1.0, v)) for v in b]})
    return {"ok": True, "elements": out, "model": resp.get("model", cfg["model"]),
            "usage": resp.get("usage") or {}}
