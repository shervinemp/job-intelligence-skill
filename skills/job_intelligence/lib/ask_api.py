#!/usr/bin/env python3
"""lib/ask_api.py — Send image + prompt to an OpenAI-compatible LLM endpoint.

Usage:
    python3 lib/ask_api.py --img screenshot.jpg --prompt "Describe this page"

On success prints the model reply. On failure prints the error."""

import argparse, base64, json, os, time, urllib.request, urllib.error

_PING_CACHE = os.path.join(os.environ.get("JI_HOME", os.path.expanduser("~/.ji")), ".ask_api_ping")


def _load_config():
    return {
        "url": os.environ.get("LLM_API_URL", "").rstrip("/"),
        "model": os.environ.get("LLM_API_MODEL", ""),
    }


def available():
    """Check if the vision endpoint is reachable. Uses cached ping (5 min TTL).
    Lightweight GET /v1/models — no model inference."""
    cfg = _load_config()
    if not cfg["url"]:
        return False
    try:
        if os.path.exists(_PING_CACHE):
            mtime = os.path.getmtime(_PING_CACHE)
            if time.time() - mtime < 300:
                return True
    except:
        pass
    try:
        req = urllib.request.Request(f"{cfg['url']}/models", method="GET")
        with urllib.request.urlopen(req, timeout=10):
            os.makedirs(os.path.dirname(_PING_CACHE), exist_ok=True)
            with open(_PING_CACHE, "w") as f:
                f.write("ok")
            return True
    except:
        return False


def ask(image_path, prompt, temperature=0.3, max_tokens=2048, file_path=None):
    """Send image file + prompt (optionally with file context) to vision API. Returns (reply, error)."""
    try:
        with open(image_path, "rb") as f:
            image_data = f.read()
    except FileNotFoundError:
        return None, f"image not found: {image_path}"
    except Exception as e:
        return None, f"reading image: {e}"
    content = []
    combined = prompt
    if file_path:
        try:
            with open(file_path, encoding="utf-8") as f:
                file_content = f.read()
            combined = f"Context from {os.path.basename(file_path)}:\n{file_content[:3000]}\n\n{prompt}"
        except Exception:
            pass
    content.append({"type": "text", "text": combined})
    return ask_bytes(image_data, content, temperature, max_tokens)


def ask_bytes(image_data, prompt_or_content, temperature=0.3, max_tokens=2048):
    """Send raw image bytes + prompt/content to vision API. Returns (reply, error)."""
    cfg = _load_config()
    if not cfg["url"]:
        return None, "LLM_API_URL not set"
    return _vision(image_data, prompt_or_content, temperature, max_tokens, cfg)


def ask_chunked(image_data, prompt, temperature=0.3, max_tokens=2048,
                max_chunk_height=1800, overlap=150):
    """Send image to vision API, auto-chunking if taller than max_chunk_height.
    Each chunk is sent with section context, then observations are consolidated.
    Falls back to ask_bytes() if PIL is not available or image is small."""
    cfg = _load_config()
    if not cfg["url"]:
        return None, "LLM_API_URL not set"
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
        return data["choices"][0]["message"]["content"], None
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}: {e.read().decode()[:200]}"
    except urllib.error.URLError as e:
        return None, f"connection failed: {e.reason}"
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        return None, f"bad response: {e}"
    except Exception as e:
        return None, str(e)


def _vision(image_data, prompt_or_content, temperature, max_tokens, cfg):
    """Send image bytes + text/content to vision API."""
    b64 = base64.b64encode(image_data).decode()
    mime = "image/jpeg" if image_data[:2] == b"\xff\xd8" else "image/png"
    if isinstance(prompt_or_content, str):
        content = [{"type": "text", "text": prompt_or_content},
                   {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}]
    else:
        # content is already a list — append image
        content = list(prompt_or_content)
        content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})
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
    parser.add_argument("--file", help="Path to context file (HTML, text, etc.) to include as context")
    parser.add_argument("--temperature", type=float, default=0.3)
    args = parser.parse_args()

    reply, err = ask(args.img, args.prompt, temperature=args.temperature, file_path=args.file)
    if err:
        print(err)
    else:
        print(reply)
