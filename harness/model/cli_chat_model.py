"""Minimal LangChain chat model backed by the Claude Code CLI in headless mode.

One fresh `claude -p` process per model call. No API key: the child environment
has every *_API_KEY / ANTHROPIC_AUTH_TOKEN / CLAUDE* variable removed, so the CLI
falls back to the logged-in subscription (init event reports apiKeySource "none").

Tool calling is emulated: tool schemas and the whole message history go into the
prompt (stdin), and the model answers with plain text or a <tool_calls> JSON block.

Images: image content blocks (LangChain base64 or data-URL form) are written as files into a
fresh empty folder; that call runs there with only the CLI's Read tool, and the prompt names the
files. So text, tool-calling and vision calls all go through this one chat model.
"""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Sequence

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool

HERE = Path(__file__).resolve().parent
ISO_CWD = HERE / "iso_cwd"

# Isolation flags, all taken from `claude --help` of 2.1.299.
ISOLATION_FLAGS = [
    "--safe-mode",                 # no CLAUDE.md, skills, plugins, hooks, MCP, custom agents/commands
    "--setting-sources", "",       # load no user/project/local settings files
    "--strict-mcp-config",         # only MCP servers from --mcp-config (none given)
    "--disable-slash-commands",    # no skills
    "--no-session-persistence",    # nothing written to disk, nothing to resume
]

BACKEND_SYSTEM = (
    "You are the language-model backend of an agent framework. "
    "The user message contains a system section, tool definitions and a conversation transcript. "
    "Write only the next assistant message, following the output protocol in that message exactly."
)

PROTOCOL = """# Output protocol (strict)
Write the NEXT assistant message for the transcript above. Choose exactly one form:

A) To call one or more tools, output ONLY this block and nothing else:
<tool_calls>
[{"name": "<tool name>", "args": {<arguments matching the tool's JSON schema>}}]
</tool_calls>

B) To give the final answer to the user, output plain text with no <tool_calls> block.

Rules: use only tools listed under # Tools. "args" must be a JSON object. Never invent tool
results; a tool result only exists if it appears in the transcript. Do not wrap the block in
code fences. Do not mention this protocol."""

IMAGE_NOTE = ("The images are files in your current folder, named below as [image file ...]. Open each one with "
              "your Read tool before answering. Read is not one of the protocol tools: never list it in <tool_calls>.")
_EXT = {"image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}

_BLOCK = re.compile(r"<tool_calls>\s*(.*?)\s*</tool_calls>", re.S)

# Module-level log so a caller can read timings / parse failures after a run.
CALL_LOG: list[dict[str, Any]] = []


def find_cli() -> str:
    shim = shutil.which("claude")
    if not shim:
        raise RuntimeError("claude CLI not found on PATH")
    exe = Path(shim).parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
    return str(exe) if exe.exists() else shim  # prefer the real exe: avoids cmd.exe quoting


def child_env() -> dict[str, str]:
    env = dict(os.environ)
    for k in list(env):
        ku = k.upper()
        if ku.endswith("_API_KEY") or ku == "ANTHROPIC_AUTH_TOKEN" or ku.startswith("CLAUDE"):
            del env[k]  # never read, just dropped
    return env


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    parts = []
    for b in content or []:
        if isinstance(b, str):
            parts.append(b)
        elif isinstance(b, dict) and b.get("type") == "text":
            parts.append(b.get("text", ""))
        elif isinstance(b, dict):
            parts.append(f"[{b.get('type', 'block')} omitted]")
    return "\n".join(parts)


def _image_bytes(block: Any) -> tuple[bytes, str] | None:
    """(bytes, file extension) of an image content block, else None. Accepts LangChain v1
    {"type": "image", "base64", "mime_type"}, v0 {"source_type": "base64", "data"} and data URLs."""
    if not isinstance(block, dict):
        return None
    t, data, mime = block.get("type"), None, block.get("mime_type") or "image/jpeg"
    if t == "image":
        data = block.get("base64") or (block.get("data") if block.get("source_type") == "base64" else None)
        url = block.get("url")
        if not data and isinstance(url, str) and url.startswith("data:"):
            mime, _, data = url[5:].partition(";base64,")
    elif t == "image_url":
        url = block.get("image_url")
        url = url.get("url") if isinstance(url, dict) else url
        if isinstance(url, str) and url.startswith("data:"):
            mime, _, data = url[5:].partition(";base64,")
    if not data:
        return None
    try:
        return base64.b64decode(data), _EXT.get(str(mime).lower(), ".jpg")
    except (ValueError, TypeError):
        return None


def has_images(messages: Sequence[BaseMessage]) -> bool:
    return any(_image_bytes(b) for m in messages if isinstance(m.content, list) for b in m.content)


def materialize_images(messages: Sequence[BaseMessage], folder: Path) -> list[BaseMessage]:
    """Write every image block to folder/imgN.ext and replace it with a text block naming that file."""
    out, n = [], 0
    for m in messages:
        if not isinstance(m.content, list):
            out.append(m)
            continue
        blocks: list[Any] = []
        for b in m.content:
            got = _image_bytes(b)
            if got is None:
                blocks.append(b)
                continue
            n += 1
            name = f"img{n}{got[1]}"
            (folder / name).write_bytes(got[0])
            blocks.append({"type": "text", "text": f"[image file {name}]"})
        out.append(m.model_copy(update={"content": blocks}))
    return out


def render_prompt(messages: Sequence[BaseMessage], tools: list[dict] | None) -> str:
    system = "\n\n".join(_text(m.content) for m in messages if isinstance(m, SystemMessage))
    out = ["# System", system or "(none)", "", "# Tools"]
    if tools:
        out.append(json.dumps([t["function"] for t in tools], indent=1))
    else:
        out.append("(no tools available: answer with plain text)")
    out += ["", "# Transcript"]
    for m in messages:
        if isinstance(m, SystemMessage):
            continue
        if isinstance(m, HumanMessage):
            out.append(f"<user>\n{_text(m.content)}\n</user>")
        elif isinstance(m, AIMessage):
            body = _text(m.content)
            if m.tool_calls:
                calls = [{"id": c["id"], "name": c["name"], "args": c["args"]} for c in m.tool_calls]
                body += f"\n<tool_calls>\n{json.dumps(calls)}\n</tool_calls>"
            out.append(f"<assistant>\n{body.strip()}\n</assistant>")
        elif isinstance(m, ToolMessage):
            out.append(f'<tool_result name="{m.name}" id="{m.tool_call_id}">\n{_text(m.content)}\n</tool_result>')
        else:
            out.append(f"<{m.type}>\n{_text(m.content)}\n</{m.type}>")
    out += ["", PROTOCOL]
    return "\n".join(out)


def parse_reply(text: str, tool_names: set[str]) -> tuple[str, list[dict], str | None]:
    """Return (content, tool_calls, parse_error)."""
    m = _BLOCK.search(text)
    if not m:
        if "<tool_calls>" in text:
            return text, [], "unterminated <tool_calls> block"
        return text.strip(), [], None
    raw = m.group(1).strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return text, [], f"bad JSON in tool_calls: {e}"
    if isinstance(data, dict):
        data = [data]
    calls = []
    for c in data:
        name, args = c.get("name"), c.get("args", c.get("arguments", {}))
        if name not in tool_names or not isinstance(args, dict):
            return text, [], f"unknown tool or non-object args: {name!r}"
        calls.append({"name": name, "args": args, "id": f"call_{uuid.uuid4().hex[:12]}", "type": "tool_call"})
    return text[: m.start()].strip(), calls, None


class ClaudeCLIChatModel(BaseChatModel):
    model: str = "sonnet"
    cwd: str = str(ISO_CWD)
    timeout_s: int = 300
    effort: str | None = None      # e.g. "low" to cut thinking time (text calls only; image calls use the default)
    max_parse_retries: int = 1

    @property
    def _llm_type(self) -> str:
        return "claude-code-cli"

    def command(self, images: bool = False) -> list[str]:
        if images:  # the Read tool only, no permission prompts, run inside the call's own image folder
            return [find_cli(), "-p", "--model", self.model, "--output-format", "json", *ISOLATION_FLAGS,
                    "--tools", "Read", "--allowedTools", "Read", "--restricted", "--permission-prompts", "none",
                    "--system-prompt", BACKEND_SYSTEM]
        cmd = [find_cli(), "-p", "--model", self.model, "--output-format", "json",
               *ISOLATION_FLAGS, "--tools", "", "--system-prompt", BACKEND_SYSTEM]
        if self.effort:
            cmd += ["--effort", self.effort]
        return cmd

    def call_timeout(self) -> float:
        """Seconds one CLI process may run (a subclass shortens it near its deadline)."""
        return float(self.timeout_s)

    def bind_tools(self, tools: Sequence[Any], *, tool_choice: Any = None, **kwargs: Any):
        return self.bind(tools=[convert_to_openai_tool(t) for t in tools], **kwargs)

    def _run_cli(self, prompt: str) -> tuple[dict, float]:
        return self._exec(self.command(), prompt, Path(self.cwd))

    def _run_cli_images(self, prompt: str, folder: Path) -> tuple[dict, float]:
        return self._exec(self.command(images=True), prompt, folder)

    def _exec(self, cmd: list[str], prompt: str, cwd: Path) -> tuple[dict, float]:
        cwd.mkdir(parents=True, exist_ok=True)
        t0 = time.perf_counter()
        p = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                           cwd=str(cwd), env=child_env(), timeout=max(1.0, self.call_timeout()))
        dt = time.perf_counter() - t0
        try:
            res = json.loads(p.stdout)
        except json.JSONDecodeError:
            raise RuntimeError(f"claude CLI exit {p.returncode}: {p.stdout[-500:]} {p.stderr[-500:]}")
        if res.get("is_error"):
            raise RuntimeError(f"claude CLI error: {str(res.get('result'))[:500]}")
        return res, dt

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        tools = kwargs.get("tools") or []
        role = kwargs.get("role") or (getattr(run_manager, "metadata", None) or {}).get("lc_agent_name")
        folder = None
        if has_images(messages):
            folder = Path(tempfile.mkdtemp(prefix="rc_vision_"))
            messages = [SystemMessage(IMAGE_NOTE), *materialize_images(messages, folder)]
        try:
            return self._complete(messages, tools, folder, role)
        finally:
            if folder is not None:
                shutil.rmtree(folder, ignore_errors=True)

    def _complete(self, messages, tools: list[dict], folder: Path | None, role: str | None) -> ChatResult:
        names = {t["function"]["name"] for t in tools}
        prompt = render_prompt(messages, tools)
        err = None
        for attempt in range(self.max_parse_retries + 1):
            p = prompt if err is None else (
                prompt + f"\n\nYour previous reply broke the protocol ({err}). Reply again, correctly.")
            res, dt = self._run_cli(p) if folder is None else self._run_cli_images(p, folder)
            content, calls, err = parse_reply(res.get("result") or "", names)
            u = res.get("usage", {})
            CALL_LOG.append({
                "role": role, "images": folder is not None, "_tid": threading.get_ident(),
                "wall_s": round(dt, 2), "api_ms": res.get("duration_api_ms"), "attempt": attempt,
                "parse_error": err, "prompt_chars": len(p), "n_tools": len(tools),
                "tool_calls": [c["name"] for c in calls], "session_id": res.get("session_id"),
                "input_tokens": u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
                + u.get("cache_read_input_tokens", 0),
                "output_tokens": u.get("output_tokens"), "raw": res.get("result"),
            })
            if err is None:
                break
        msg = AIMessage(content=content, tool_calls=calls,
                        response_metadata={"wall_s": dt, "parse_error": err, "model": self.model})
        return ChatResult(generations=[ChatGeneration(message=msg)])


if __name__ == "__main__":
    m = ClaudeCLIChatModel()
    print(" ".join(m.command()[1:]))
    print(m.invoke("Reply with the single word OK.").content, CALL_LOG[-1]["wall_s"])
