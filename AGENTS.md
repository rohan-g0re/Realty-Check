# AGENTS.md — rules for every agent working in this repo

## What this repo is
- Reality Check: a Chrome extension plus a local server and a deepagents harness. On any rental listing page it finds an address and, on request, fact-checks what the page claims about that location against a building record, a 3D scan, aerial imagery and county data.
- Built at HackPrinceton, Nov 13–15 2026. Code only. This repo is public at submission time.
- Contract: `docs/CONTRACT.md` (data shapes, tool signatures, events). Build order: `docs/DELIVERABLES.md`. Log: `docs/DECISIONS.md`.
- Private driver folder (read-only from here): `$RC_PRIVATE_DIR` (a private folder outside the repo). It holds the sponsor kit, `.env` with keys, earlier probes and verification results. Code reads its path from that environment variable, never hard-coded.

## Hard rules
- No secrets, keys, tokens, zip passwords or signed URLs in any file, commit, log line or report. Keys are read at run time from `$RC_PRIVATE_DIR/.env`.
- No sponsor data in this repo: no kit records, no Matterport or Vexcel imagery, no cached run outputs. Those go under `$RC_PRIVATE_DIR/rc_cache/` (outside this repo). Test fixtures in `tests/fixtures/` are synthetic or public only.
- Never modify anything in the private driver folder except by creating files under `rc_cache/` there.
- Matterport: read-only queries only, no mutations, at most 2 requests per second. Vexcel: at most 2 requests per second.
- The harness model is the Claude Code CLI in headless Sonnet mode through `harness/model/cli_chat_model.py`. No API keys of any provider are read or used.
- Houston 311 free text can contain caller names and phone numbers: never surface it; use counts and categories only.

## File ownership
- Each task owns the folders named in its brief. Disjoint by design.
- Need a file outside your ownership: stop and report; do not edit it.
- `docs/CONTRACT.md`, `harness/model/`, `pyproject.toml`, `AGENTS.md`, `.gitignore` belong to the orchestrator. If the contract is wrong or impossible, report the exact line; do not invent a variant.

## Git
- Agents never commit, push, merge, or create branches. Report "ready" and stop. The orchestrator commits and pushes.
- Fixture file names never end in `.log`.

## Engineering rules
- Implement the contract exactly: names, signatures, record shapes.
- Plain Python functions first, each testable alone with a fixture; the harness wires them afterwards.
- Keep it boring: small functions, no new framework, no clever abstractions.
- A tool never guesses. No evidence means `status: "no_evidence"`, with the reason.
- Python: `.venv/Scripts/python.exe` (relative to the repo root). Do not pip-install without listing the package in your report; tell the orchestrator instead of editing `pyproject.toml`.
- Shell is Git Bash on Windows 11 ARM64.

## Before reporting ready
1. Your module's test command passes; quote the last lines verbatim.
2. `git status --short` shows changes only in folders you own.
3. Grep your files for key-shaped strings and absolute paths into the private folder: none.

## Report (in this order)
files changed; test command with verbatim result; acceptance items each ticked with the test that proves it; anything unverified; packages needed; decision bullets for `docs/DECISIONS.md`.
