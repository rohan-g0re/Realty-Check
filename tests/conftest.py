"""Server tests never start real 3D spot picks in the background (tour.prewarm); test_tour turns it on with a stub picker."""
import pytest

from server import tour


@pytest.fixture(autouse=True)
def _no_spot_prewarm(monkeypatch):
    monkeypatch.setattr(tour, "PREWARM", False)
