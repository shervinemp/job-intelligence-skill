#!/usr/bin/env python3
"""lib/ask_api.py — Send image + prompt to an OpenAI-compatible LLM endpoint.

Usage:
    python3 lib/ask_api.py --img screenshot.jpg --prompt "Describe this page"

On success prints the model reply. On failure prints the error."""

import argparse, base64, json, os, sys, time, urllib.request, urllib.error

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


def ask(image_path, prompt, temperature=0.3, max_tokens=2048):
    cfg = _load_config()
    if not cfg["url"]:
        return None, "LLM_API_URL not set"

    messages = [{"role": "user", "content": prompt}]
    if image_path:
        try:
            with open(image_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
        except FileNotFoundError:
            return None, f"image not found: {image_path}"
        except Exception as e:
            return None, f"reading image: {e}"

        ext = os.path.splitext(image_path)[1].lower()
        mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                "webp": "image/webp", "gif": "image/gif"}.get(ext, "image/png")
        messages = [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
        ]}]

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
        with urllib.request.urlopen(req, timeout=120) as resp:
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
