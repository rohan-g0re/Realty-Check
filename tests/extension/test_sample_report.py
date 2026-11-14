"""The extension's sample report (extension/report/) is server/sample_report.py's output, and fits an MV3 page.

Regenerate: .venv/Scripts/python.exe -m server.sample_report
Run: .venv/Scripts/python.exe -m pytest tests/extension/test_sample_report.py -q
"""
import re
from pathlib import Path

from server.sample_report import OUT, SAMPLE_ADDRESS, build, load_events

REPO = Path(__file__).resolve().parents[2]


def _read(name: str) -> str:
    return (OUT / name).read_text(encoding="utf-8").replace("\r\n", "\n")  # git may check out with CRLF


def test_committed_files_match_the_generator():
    for name, text in build().items():
        assert (OUT / name).exists(), f"missing extension/report/{name}: run python -m server.sample_report"
        assert _read(name) == text, f"extension/report/{name} is out of date: run python -m server.sample_report"


def test_no_inline_executable_script_or_handlers():
    html = _read("report.html")
    for attrs, body in re.findall(r"<script(\s[^>]*)?>(.*?)</script>", html, re.S | re.I):
        if re.search(r"type\s*=\s*['\"]?application/json", attrs or "", re.I):
            continue
        assert "src=" in (attrs or "") and not body.strip(), f"inline script: <script{attrs}>"
    assert not re.search(r"<[^>]*\son[a-z]+\s*=", html, re.I), "inline event handler attribute"
    assert "javascript:" not in html.lower()
    for src in re.findall(r"<script[^>]*\ssrc=['\"]?([^'\" >]+)", html):
        assert (OUT / src).exists(), src


def test_page_is_design_a_with_the_sample_run():
    html = _read("report.html")
    assert "id=cf-field" in html and "id=cf-data" in html  # the claim field
    assert "id=mismatch" in html and "id=extra" in html
    for cid in ("c1", "c2", "c3"):  # the three findings, each with its #claim-<id> anchor
        assert f"id='claim-{cid}'" in html
    assert "Sample result" in html and SAMPLE_ADDRESS in html
    assert "/img/" not in html and "/tour/" not in html  # nothing that needs the local server
    assert not re.search(r"\b(fake|lie|lies|scam)\b", re.sub(r"<style>.*?</style>", "", html, flags=re.S), re.I)


def test_sample_events_are_invented_and_complete():
    ev = load_events("1 Test Way")
    kinds = [e["type"] for e in ev]
    assert kinds[0] == "run_started" and kinds[-1] == "done"
    assert kinds.index("fill_in") > max(i for i, k in enumerate(kinds) if k == "verdict")
    assert ev[0]["address"] == "1 Test Way"
    assert "{{address}}" not in str(ev)
