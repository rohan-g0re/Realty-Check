"""One-shot calls through the harness chat model (Claude Code CLI, headless Sonnet, isolated, no API key).

ask_text / ask_json: text in, text or parsed JSON out.  ask_images: same, with image files.
Every call goes through ClaudeCLIChatModel, the chat model the deep agent runs on. Inside a run the
agent sets CURRENT_MODEL to its own budgeted model, so the claim reading and photo checks its tools make
share that model's deadline and timing log; outside a run a fresh model is used. Each call is a fresh
CLI process with no memory of any other call.
"""
from __future__ import annotations

import base64
import contextvars
import json
import re
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from .cli_chat_model import ClaudeCLIChatModel, _text

PLAIN_SYSTEM = "You are a careful analyst. Follow the user's output format exactly. No preamble."

# The run's chat model (set by harness.graph.agent for the duration of a run), and a role tag for its log.
CURRENT_MODEL: contextvars.ContextVar[Any] = contextvars.ContextVar("rc_current_model", default=None)
ROLE: contextvars.ContextVar[str | None] = contextvars.ContextVar("rc_model_role", default=None)

_MIME = {".png": "image/png", ".webp": "image/webp", ".gif": "image/gif"}


def _model(model: str, timeout_s: int):
    return CURRENT_MODEL.get() or ClaudeCLIChatModel(model=model, timeout_s=timeout_s)


def ask_text(prompt: str, system: str = PLAIN_SYSTEM, model: str = "sonnet", timeout_s: int = 180) -> str:
    msg = _model(model, timeout_s).invoke([SystemMessage(system), HumanMessage(prompt)], role=ROLE.get() or "ask")
    return _text(msg.content)


def parse_json(text: str) -> Any:
    """Parse the first JSON object or array in text (tolerates code fences and prose around it)."""
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.S)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    for opener, closer in (("{", "}"), ("[", "]")):
        i, j = t.find(opener), t.rfind(closer)
        if 0 <= i < j:
            try:
                return json.loads(t[i:j + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError(f"no JSON in model reply: {text[:200]}")


def ask_json(prompt: str, system: str = PLAIN_SYSTEM, model: str = "sonnet", retries: int = 1) -> Any:
    err = ""
    for _ in range(retries + 1):
        text = ask_text(prompt + (f"\n\nYour last reply was not valid JSON ({err}). Reply with JSON only." if err else ""),
                        system, model)
        try:
            return parse_json(text)
        except ValueError as e:
            err = str(e)[:120]
    raise ValueError(err)


def image_message(image_paths: list[str], question: str) -> HumanMessage:
    """A user message carrying the images as base64 content blocks, then the question."""
    blocks: list[Any] = [{"type": "text", "text": "Look at these images, then answer."}]
    for p in image_paths:
        data = base64.b64encode(Path(p).read_bytes()).decode("ascii")
        blocks.append({"type": "image", "base64": data, "mime_type": _MIME.get(Path(p).suffix.lower(), "image/jpeg")})
    blocks.append({"type": "text", "text": question})
    return HumanMessage(content=blocks)


def ask_images(image_paths: list[str], question: str, model: str = "sonnet", timeout_s: int = 240) -> str:
    """Ask a question about image files, through the chat model's image path (files in a fresh folder, Read only)."""
    msg = _model(model, timeout_s).invoke([SystemMessage(PLAIN_SYSTEM), image_message(image_paths, question)],
                                          role=ROLE.get() or "vision")
    return _text(msg.content)
