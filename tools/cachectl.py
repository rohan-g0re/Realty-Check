"""One switch for every result cache: RC_NO_CACHE=1 skips each cache READ so the work is really redone.

Writes still happen, in place (same paths as always), so the report page of a no-cache run serves that run's
own pictures and a later normal run sees the fresh results. The kit dataset (rc_cache/kit), the HCAD bulk data
(rc_cache/paper/hcad) and saved runs (rc_cache/runs) are not caches and ignore this switch.
Unset (or 0/false/no/off): behaviour is unchanged.
"""
from __future__ import annotations

import os
from pathlib import Path

ENV = "RC_NO_CACHE"


def enabled() -> bool:
    """True when result caches may be read (the default). Read from the environment on every call."""
    return os.environ.get(ENV, "").strip().lower() not in ("1", "true", "yes", "on")


def read_ok() -> bool:
    """Same as enabled(): may a cached result be read now?"""
    return enabled()


def hit(path: Path | str | None) -> bool:
    """Drop-in for `path.exists()` at a cache read site: False whenever caches are off."""
    return bool(path) and enabled() and Path(path).exists()
