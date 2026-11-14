"""Manual check: POST a sample snapshot to a running server and print events as they arrive.

    python -m server.smoke [base_url]
"""
from __future__ import annotations

import json
import sys

import httpx

SAMPLE = {
    "url": "https://example.com/listing/418-linden-grove-ave",
    "title": "418 Linden Grove Ave Apartments",
    "address_text": "418 Linden Grove Ave, Houston, TX 77006",
    "text": "Plan E. 2 Beds, 2 Baths, 1,100 sq ft. 5-story building. Built in 2016. Resort-style pool.",
    "jsonld": [{"@type": "ApartmentComplex"}],
}


def main(base: str = "http://127.0.0.1:8787") -> None:
    with httpx.stream("POST", f"{base}/check", json=SAMPLE, timeout=None) as r:
        r.raise_for_status()
        for line in r.iter_lines():
            if line.startswith("data: "):
                ev = json.loads(line[6:])
                rest = {k: v for k, v in ev.items() if k != "type"}
                print(f"{ev['type']:<12} {json.dumps(rest)[:160]}", flush=True)
            elif line.startswith(":"):
                print("(ping)", flush=True)


if __name__ == "__main__":
    main(*sys.argv[1:2])
