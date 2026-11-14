"""look(): ask the vision model fixed-field questions about images (docs/CONTRACT.md section 3).

The only model access is harness.model.ask.ask_images (Claude Code CLI, headless, Read tool only).
Never raises: failures come back as status "error" with notes.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image

import rc_common
from harness.model import ask
from tools import cachectl

MAX_IMAGES = 6
MAX_SIDE = 1568


def build_prompt(question: str, fields: dict[str, str]) -> str:
    """Prompt asking for one strict JSON object with exactly the given fields."""
    lines = "\n".join(f'- "{name}": {desc}' for name, desc in fields.items())
    skeleton = json.dumps({"answer": {name: "..." for name in fields}, "confidence": 0.0, "notes": "..."})
    return (
        f"Question: {question}\n\n"
        f"Fill these fields in \"answer\":\n{lines}\n\n"
        "Rules:\n"
        "- Answer only from what is visible in the images. Do not guess from outside knowledge.\n"
        "- Use null for any field that cannot be determined from the images.\n"
        "- confidence is a number from 0 to 1 for the answer as a whole.\n"
        "- notes is one sentence on what you saw.\n"
        "- Reply with JSON only, no code fences, no other text, in exactly this shape:\n"
        f"{skeleton}"
    )


def normalize(reply: Any, fields: dict[str, str]) -> dict:
    """Turn a parsed model reply into the contract shape; missing fields become null."""
    if not isinstance(reply, dict):
        raise ValueError("model reply is not a JSON object")
    raw = reply.get("answer") if isinstance(reply.get("answer"), dict) else {}
    answer = {name: raw.get(name) for name in fields}
    try:
        conf = min(1.0, max(0.0, float(reply.get("confidence"))))
    except (TypeError, ValueError):
        conf = 0.0
    return {"status": "ok", "answer": answer, "confidence": conf, "notes": str(reply.get("notes") or "")}


def _error(fields: dict[str, str], notes: str) -> dict:
    return {"status": "error", "answer": {name: None for name in fields}, "confidence": 0.0, "notes": notes}


def _cache_file(paths: list[str], question: str, fields: dict[str, str]) -> Path | None:
    if not os.environ.get("RC_PRIVATE_DIR"):
        return None
    h = hashlib.sha256()
    for p in paths:
        h.update(Path(p).read_bytes())
        h.update(b"\0")
    h.update(question.encode("utf-8"))
    h.update(json.dumps(fields, sort_keys=True).encode("utf-8"))
    return rc_common.cache_dir("vision") / f"{h.hexdigest()[:32]}.json"


def _downscale(paths: list[str], work: Path) -> list[str]:
    """Copy each image into work, shrunk to at most MAX_SIDE on the long side. Originals untouched."""
    out = []
    for i, src in enumerate(paths):
        with Image.open(src) as im:
            im.load()
            if max(im.size) > MAX_SIDE:
                im.thumbnail((MAX_SIDE, MAX_SIDE))
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            dst = work / f"in{i + 1}.jpg"
            im.save(dst, "JPEG", quality=90)
        out.append(str(dst))
    return out


def look(image_paths: list[str], question: str, fields: dict[str, str]) -> dict:
    """Ask a question about up to 6 images; return {status, answer, confidence, notes}."""
    paths = [str(p) for p in (image_paths or [])]
    extra = ""
    if len(paths) > MAX_IMAGES:
        extra = f" ({len(paths) - MAX_IMAGES} extra images ignored; at most {MAX_IMAGES} are used.)"
        paths = paths[:MAX_IMAGES]
    if not paths:
        return _error(fields, "No images given.")
    missing = [p for p in paths if not Path(p).is_file()]
    if missing:
        return _error(fields, f"Image not found: {Path(missing[0]).name}")

    try:
        cache = _cache_file(paths, question, fields)
    except Exception as e:  # unreadable image or cache folder
        return _error(fields, f"Could not read images: {e}")
    if cachectl.hit(cache):
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass

    work = Path(tempfile.mkdtemp(prefix="rc_look_"))
    try:
        try:
            small = _downscale(paths, work)
        except Exception as e:
            return _error(fields, f"Could not open image: {e}")
        prompt = build_prompt(question, fields)
        err = ""
        for attempt in range(2):  # one retry on an unparseable reply
            p = prompt if not err else prompt + f"\n\nYour last reply was not valid JSON ({err}). Reply with the JSON object only."
            try:
                text = ask.ask_images(small, p)
            except Exception as e:
                return _error(fields, f"Vision model call failed: {str(e)[:200]}")
            try:
                result = normalize(ask.parse_json(text), fields)
                break
            except ValueError as e:
                err = str(e)[:120]
        else:
            return _error(fields, f"Model reply was not usable JSON after a retry: {err}")
    finally:
        shutil.rmtree(work, ignore_errors=True)

    result["notes"] += extra
    if cache:
        try:
            cache.write_text(json.dumps(result, indent=1), encoding="utf-8")
        except OSError:
            pass
    return result
