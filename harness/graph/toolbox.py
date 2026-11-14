"""Toolbox: every outside function the run uses, behind one injectable object.

Toolbox.real() imports the real modules lazily. A module that fails to import becomes a
stand-in that returns Evidence status "error" (or an empty / no-match value), so a run
degrades instead of crashing. Toolbox.fake() loads canned synthetic data from tests/graph/fakes.py.
"""
from __future__ import annotations

import importlib
import importlib.util
from dataclasses import dataclass
from typing import Any, Callable

from rc_common import REPO, evidence


def _vision_error(msg: str) -> dict:
    return {"status": "error", "answer": {}, "confidence": 0.0, "notes": msg}


@dataclass
class Toolbox:
    extract_claims: Callable[[dict], list]
    match_address: Callable[..., dict]
    geocode: Callable[[str], Any]
    record_evidence: Callable[[dict], dict]
    scan_summary: Callable[[str], dict]
    scan_stills: Callable[..., dict]
    aerial_images: Callable[..., dict]
    building_height: Callable[[float, float], dict]
    county_record: Callable[..., dict]
    count_floors: Callable[[list], dict]
    read_floorplan: Callable[[list], dict]
    check_room: Callable[[list, str], dict]
    find_features: Callable[..., dict] = None  # presence of amenities in photos; None = not available
    kit_facts: Callable[[str], Any] = None     # extra kit facts for "Fill me in"; None = not available

    def __post_init__(self):
        if self.find_features is None:
            self.find_features = lambda *a, **k: _vision_error("find_features not provided")
        if self.kit_facts is None:
            self.kit_facts = lambda *a, **k: None

    @classmethod
    def real(cls) -> "Toolbox":
        def load(module: str, name: str, fallback: Callable[[str], Callable]) -> Callable:
            try:
                return getattr(importlib.import_module(module), name)
            except Exception as e:  # missing module, missing name, import-time error
                return fallback(f"{module}.{name} unavailable: {type(e).__name__}: {e}"[:200])

        def ev(source: str):
            return lambda msg: (lambda *a, **k: evidence(source, "error", "This check is not available right now.",
                                                         detail=msg))

        return cls(
            extract_claims=load("harness.claims", "extract_claims", lambda msg: lambda *a, **k: []),
            match_address=load("tools.kit", "match_address", lambda msg: lambda *a, **k: {
                "status": "no_match", "confidence": 0.0, "how": msg}),
            geocode=load("tools.paper", "geocode", lambda msg: lambda *a, **k: None),
            record_evidence=load("tools.kit", "record_evidence", ev("record")),
            scan_summary=load("tools.scan", "scan_summary", ev("scan")),
            scan_stills=load("tools.scan", "scan_stills", ev("scan")),
            aerial_images=load("tools.aerial", "aerial_images", ev("aerial")),
            building_height=load("tools.aerial", "building_height", ev("aerial")),
            county_record=load("tools.paper", "county_record", ev("county")),
            count_floors=load("tools.vision", "count_floors", lambda msg: lambda *a, **k: _vision_error(msg)),
            read_floorplan=load("tools.vision", "read_floorplan", lambda msg: lambda *a, **k: _vision_error(msg)),
            check_room=load("tools.vision", "check_room", lambda msg: lambda *a, **k: _vision_error(msg)),
            find_features=load("tools.vision", "find_features", lambda msg: lambda *a, **k: _vision_error(msg)),
            kit_facts=load("tools.kit", "kit_facts", lambda msg: lambda *a, **k: None),
        )

    @classmethod
    def fake(cls, **overrides: Callable) -> "Toolbox":
        """Synthetic scenario from tests/graph/fakes.py (2 Beds vs 1BR W Den, 5 vs 6 floors, ...)."""
        path = REPO / "tests" / "graph" / "fakes.py"
        spec = importlib.util.spec_from_file_location("rc_graph_fakes", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return cls(**{**mod.FAKE_FUNCTIONS, **overrides})
