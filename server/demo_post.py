"""Post a demo snapshot to a running server; print each event's type and the final headline.

    python -m server.demo_post [houston_building|la_unit|path/to/snapshot.json] [base_url]

base_url defaults to http://127.0.0.1:$RC_PORT (8787).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "public"


def main(which: str = "houston_building", base: str | None = None) -> int:
    base = base or f"http://127.0.0.1:{os.environ.get('RC_PORT') or 8787}"
    path = Path(which) if which.endswith(".json") else FIXTURES / f"{which}.json"
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    done = None
    with httpx.stream("POST", f"{base}/check", json=snapshot, timeout=None) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if not line.startswith("data: "):
                continue
            ev = json.loads(line[6:])
            extra = ev.get("verdict", {}).get("verdict", "") if ev["type"] == "verdict" else ""
            print(ev["type"], extra, flush=True)
            if ev["type"] == "done":
                done = ev
    if not done:
        print("no done event")
        return 1
    print("headline:", done["headline"])
    print("counts:", json.dumps(done["counts"]))
    print("report:", done["report_url"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:3]))
