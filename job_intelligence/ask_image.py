#!/usr/bin/env python3
"""ask_image.py — Send image + prompt to an OpenAI-compatible LLM endpoint.

Usage:
    python3 ask_image.py --img screenshot.jpg --prompt "Describe this page"

On success prints the model reply. On failure prints the error."""

import argparse, base64, json, os, sys, urllib.request, urllib.error


def _load_config():
    return {
        "url": os.environ.get("LLAMA_VISION_URL", "").rstrip("/"),
        "model": os.environ.get("LLAMA_VISION_MODEL", ""),
    }


def ask(image_path, prompt, temperature=0.3, max_tokens=2048):
    cfg = _load_config()
    if not cfg["url"]:
        return None, "LLAMA_VISION_URL not set"

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

    body = json.dumps({
        "model": cfg["model"],
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            ],
        }],
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
        prog="ask_image.py",
        description="Send image + prompt to an OpenAI-compatible LLM endpoint.",
    )
    parser.add_argument("--img", required=True, help="Path to image file")
    parser.add_argument("--prompt", required=True, help="Question about the image")
    parser.add_argument("--temperature", type=float, default=0.3)
    args = parser.parse_args()

    reply, err = ask(args.img, args.prompt, temperature=args.temperature)
    if err:
        print(err)
    else:
        print(reply)
