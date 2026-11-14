"""Graph tests never make a real CLI call for the top-facts pick (harness.top_facts): its model call is stubbed with
an empty answer, so the rule decides and scripted-CLI role counts stay as they were. test_live keeps the real one."""
import pytest


@pytest.fixture(autouse=True)
def _no_top_facts_model(request, monkeypatch):
    if request.module.__name__.endswith("test_live"):
        return
    from harness.graph import run as run_mod
    monkeypatch.setattr(run_mod, "TOP_FACTS_LLM", lambda prompt: "{}")
