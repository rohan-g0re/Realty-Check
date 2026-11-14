"""Real-world expected values for the live tests.

The real sponsor addresses, ids and measurements never live in this repo: they sit in a JSON file in the
private folder (RC_PRIVATE_DIR). golden(section) returns one section of it, or skips the test when the
private folder, the file, or the section is missing.
"""
import json
import os
from pathlib import Path

import pytest


def _path() -> Path | None:
    cands = []
    if os.environ.get("RC_GOLDEN"):
        cands.append(Path(os.environ["RC_GOLDEN"]))
    priv = os.environ.get("RC_PRIVATE_DIR")
    if priv:
        cands += [Path(priv) / "test_golden.json", Path(priv) / "rc_cache" / "verify" / "scrub" / "test_golden.json"]
    return next((p for p in cands if p.is_file()), None)


def golden(section: str):
    if not os.environ.get("RC_PRIVATE_DIR"):
        pytest.skip("RC_PRIVATE_DIR not set")
    p = _path()
    if p is None:
        pytest.skip("private test_golden.json not found")
    data = json.loads(p.read_text(encoding="utf-8"))
    if section not in data:
        pytest.skip(f"golden section {section!r} missing")
    return data[section]
