#!/usr/bin/env python3
import sys; sys.stdout.reconfigure(encoding="utf-8", errors="replace"); del sys

"""lib/ask_api.py — Send image + prompt to an OpenAI-compatible LLM endpoint.

Usage:
    python3 lib/ask_api.py --img screenshot.jpg --prompt "Describe this page"

On success prints the model reply. On failure prints the error."""

import argparse, base64, json, os, time, urllib.request, urllib.error

_PING_CACHE = os.path.join(os.environ.get("JI_HOME", os.path.expanduser("~/.ji")), ".ask_api_ping")


def _load_config():
    """Endpoint config.

    Accepts OPENAI_API_BASE as an alias for LLM_API_URL: the deployed .env
    set OPENAI_API_BASE (a name inherited from an older integration) while
    this module only ever read LLM_API_URL, so available() returned False
    forever and the vision escape hatch was silently closed — every shadow
    header printed api=DOWN and nobody read it.
    """
    url = (os.environ.get("LLM_API_URL")
           or os.environ.get("OPENAI_API_BASE")
           or "").rstrip("/")
    return {
        "url": url,
        "model": os.environ.get("LLM_API_MODEL", ""),
    }


def _is_loopback(url):
    """True if the URL's host is a loopback/local address (localhost,
    127.x, ::1, or a bare hostname with no dot). Vision bytes (real form
    screenshots with PII) must never leave the machine, so the vision
    endpoint is required to be local — a misconfigured remote endpoint is
    refused rather than silently exfiltrating screenshots (AGENTS.md red
    line: no private data exfiltration)."""
    from urllib.parse import urlparse
    try:
        host = (urlparse(url).hostname or "").lower()
        if not host and "::1" in url:
            # Unbracketed IPv6 loopback ("http://::1:9000/v1") parses with a
            # None hostname — recognize the literal.
            host = "::1"
    except Exception:
        return False
    if host in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        return True
    if host.startswith("127.") or host == "::":
        return True
    # Bare hostname (no dot, not an IP) resolves locally via the resolver.
    if "." not in host and ":" not in host and host:
        return True
    return False


def _local_guard():
    """Refuse vision requests to a non-local endpoint. Returns the config,
    or None when the endpoint is remote (caller must not send PII bytes)."""
    cfg = _load_config()
    if not cfg["url"]:
        return None
    if not _is_loopback(cfg["url"]):
        return None
    return cfg


def available():
    """Check if the vision endpoint is reachable AND vision-safe (local).
    Uses cached ping (5 min TTL). Lightweight GET /v1/models — no inference.

    C2: a remote endpoint is NOT "available" for vision — ask_bytes would
    refuse it anyway (A3), so a cached ping must never make callers believe
    vision works when the bytes would be refused."""
    cfg = _local_guard()
    if cfg is None:
        return False
    try:
        if os.path.exists(_PING_CACHE):
            mtime = os.path.getmtime(_PING_CACHE)
            if time.time() - mtime < 300:
                return True
    except OSError:
        pass
    try:
        req = urllib.request.Request(f"{cfg['url']}/models", method="GET")
        with urllib.request.urlopen(req, timeout=10):
            os.makedirs(os.path.dirname(_PING_CACHE), exist_ok=True)
            with open(_PING_CACHE, "w") as f:
                f.write("ok")
            return True
    except Exception:
        # Bare `except:` here also swallowed KeyboardInterrupt/SystemExit.
        return False


def ask(image_path, prompt, temperature=0.3, max_tokens=2048):
    """Send image (or PDF) file + prompt to vision API. PDFs are auto-rendered."""
    rendered = False
    tmp_img = None
    if image_path and image_path.lower().endswith(".pdf"):
        import tempfile
        tmp_img = os.path.join(tempfile.gettempdir(), f"ask_pdf_{os.getpid()}.jpg")
        try:
            from pdf2image import convert_from_path
            imgs = convert_from_path(image_path, dpi=150, first_page=1, last_page=1)
            if imgs:
                imgs[0].save(tmp_img, "JPEG", quality=85)
                image_path = tmp_img
                rendered = True
        except ImportError:
            try:
                import fitz
                doc = fitz.open(image_path)
                if doc.page_count > 0:
                    pix = doc[0].get_pixmap(dpi=150)
                    pix.save(tmp_img)
                    image_path = tmp_img
                    rendered = True
            except ImportError:
                pass
        if not rendered:
            return None, "PDF rendering requires pdf2image or PyMuPDF — install one"
    try:
        with open(image_path, "rb") as f:
            image_data = f.read()
    except FileNotFoundError:
        return None, f"image not found: {image_path}"
    except Exception as e:
        return None, f"reading image: {e}"
    finally:
        if rendered and tmp_img and os.path.exists(tmp_img):
            try:
                os.remove(tmp_img)
            except OSError:
                pass
    return ask_bytes(image_data, prompt, temperature, max_tokens)


def ask_bytes(image_data, prompt, temperature=0.3, max_tokens=2048):
    """Send raw image bytes + prompt to vision API. No temp files needed. Returns (reply, error)."""
    cfg = _local_guard()
    if cfg is None:
        return None, ("vision bytes refused: LLM_API_URL is not a local/loopback "
                      "endpoint — screenshots must not leave the machine "
                      "(AGENTS.md red line)")
    return _vision(image_data, prompt, temperature, max_tokens, cfg)


def ask_text(prompt, temperature=0.3, max_tokens=2048, timeout=10):
    """Send text-only prompt to LLM API (no image). Returns (reply, error)."""
    cfg = _load_config()
    if not cfg["url"]:
        return None, "LLM_API_URL (or OPENAI_API_BASE) not set"
    return _text(prompt, temperature, max_tokens, cfg, timeout=timeout)


def ask_chunked(image_data, prompt, temperature=0.3, max_tokens=2048,
                max_chunk_height=1800, overlap=150):
    """Send image to vision API, auto-chunking if taller than max_chunk_height.
    Each chunk is sent with section context, then observations are consolidated.
    Falls back to ask_bytes() if PIL is not available or image is small."""
    cfg = _load_config()
    if not cfg["url"]:
        return None, "LLM_API_URL (or OPENAI_API_BASE) not set"
    dims = _jpeg_dims(image_data)
    if not dims or dims[1] <= max_chunk_height:
        return _vision(image_data, prompt, temperature, max_tokens, cfg)
    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(image_data))
        w, h = dims
        chunks = []
        n = max(1, (h - overlap) // (max_chunk_height - overlap) + 1)
        for i in range(n):
            y1 = max(0, i * (max_chunk_height - overlap))
            y2 = min(y1 + max_chunk_height, h)
            buf = io.BytesIO()
            img.crop((0, y1, w, y2)).save(buf, format="JPEG", quality=80)
            cp = f"{prompt}\n\nThis is visual section {i+1} of {n} of the page (top to bottom). Focus on this section only."
            reply, err = _vision(buf.getvalue(), cp, temperature, max_tokens, cfg)
            if err:
                return None, err
            chunks.append(reply)
        consol = (
            f"Below are observations from {n} sections of a page (top to bottom).\n\n"
            + "\n---\n".join(f"Section {i+1}:\n{r}" for i, r in enumerate(chunks))
            + f"\n\nBased on ALL sections above, answer the original question:\n{prompt}\n"
            "Give a single, precise, consolidated answer. If sections disagree, explain why."
        )
        final, err = _text(consol, temperature, min(max_tokens, 1024), cfg)
        if err:
            partials = "\n".join(f"Section {i+1}: {r[:200]}" for i, r in enumerate(chunks))
            return (f"CONSOLIDATION_FAILED — partial results:\n{partials}", None)
        return (final, None)
    except ImportError:
        return _vision(image_data, prompt, temperature, max_tokens, cfg)


def _jpeg_dims(data):
    """Read JPEG dimensions from raw bytes. No dependencies."""
    import struct
    i = 0
    while i < len(data) - 1:
        if data[i] != 0xFF:
            i += 1
            continue
        m = data[i+1]
        if m in (0xC0, 0xC1, 0xC2):
            return (struct.unpack_from('>H', data, i+7)[0],
                    struct.unpack_from('>H', data, i+5)[0])
        if m == 0xD9:
            break
        if 0xD0 <= m <= 0xD8:
            i += 2
            continue
        length = struct.unpack_from('>H', data, i+2)[0]
        i += 2 + length
    return None


def _payload(messages, temperature, max_tokens, cfg, timeout=60):
    """Build request body and call the API. Returns (reply, error)."""
    body = json.dumps({
        "model": cfg["model"],
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }).encode()
    api_url = f"{cfg['url']}/chat/completions"
    req = urllib.request.Request(
        api_url, data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
        msg = data["choices"][0]["message"]
        # Some models (DeepSeek R1, Qwen with reasoning) put thinking in
        # reason_content / reasoning_content and the answer in content.
        # If content is empty but reasoning content exists, use that instead.
        content = msg.get("content", "") or ""
        if not content.strip():
            for _rc_key in ("reasoning_content", "reason_content"):
                _rc = msg.get(_rc_key, "") or ""
                if _rc.strip():
                    content = _rc
                    break
        return content, None
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}: {e.read().decode()[:200]}"
    except urllib.error.URLError as e:
        return None, f"connection failed: {e.reason}"
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        return None, f"bad response: {e}"
    except Exception as e:
        return None, str(e)


def _vision(image_data, prompt, temperature, max_tokens, cfg):
    """Send image bytes + text to vision API."""
    b64 = base64.b64encode(image_data).decode()
    # Detect MIME from magic bytes
    mime = "image/jpeg" if image_data[:2] == b"\xff\xd8" else "image/png"
    content = [{"type": "text", "text": prompt},
               {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}]
    return _payload([{"role": "user", "content": content}], temperature, max_tokens, cfg)


def _text(prompt, temperature, max_tokens, cfg, timeout=10):
    """Send text-only prompt to API."""
    return _payload([{"role": "user", "content": prompt}], temperature, max_tokens, cfg, timeout=timeout)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        prog="lib/ask_api.py",
        description="Send image + prompt to an OpenAI-compatible LLM endpoint.",
    )
    parser.add_argument("--img", help="Path to image file (optional — text-only if omitted)")
    parser.add_argument("--prompt", required=True, help="Question about the image")
    parser.add_argument("--temperature", type=float, default=0.3)
    args = parser.parse_args()

    reply, err = ask(args.img, args.prompt, temperature=args.temperature)
    if err:
        print(err)
    else:
        print(reply)
