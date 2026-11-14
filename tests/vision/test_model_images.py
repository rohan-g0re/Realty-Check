"""Vision goes through the harness chat model: image blocks become files in a fresh folder, the CLI gets the Read
tool there, and ask_images / look() use that path. No CLI process (the run is faked). Invented images."""
from pathlib import Path

from PIL import Image

from harness.model import ask
from harness.model import cli_chat_model as ccm


def test_ask_images_runs_the_chat_model_with_files_in_its_folder(monkeypatch, tmp_path):
    img = tmp_path / "a.png"
    Image.new("RGB", (8, 8), (200, 10, 10)).save(img)
    seen = {}

    def fake_images(self, prompt, folder):
        seen["files"] = sorted(p.name for p in Path(folder).iterdir())
        seen["prompt"] = prompt
        seen["cmd"] = self.command(images=True)
        return {"result": '{"answer": {}, "confidence": 1, "notes": "red"}', "usage": {}}, 0.01

    def no_text(self, prompt):
        raise AssertionError("an image call must not take the text path")
    monkeypatch.setattr(ccm.ClaudeCLIChatModel, "_run_cli_images", fake_images)
    monkeypatch.setattr(ccm.ClaudeCLIChatModel, "_run_cli", no_text)
    monkeypatch.setattr(ccm, "find_cli", lambda: "claude")
    out = ask.ask_images([str(img)], "What colour?")
    assert "red" in out
    assert seen["files"] == ["img1.png"] and "[image file img1.png]" in seen["prompt"] and "What colour?" in seen["prompt"]
    assert seen["cmd"][seen["cmd"].index("--tools") + 1] == "Read"
    assert ccm.CALL_LOG[-1]["images"] is True and ccm.CALL_LOG[-1]["role"] == "vision"


def test_run_model_is_used_when_set(monkeypatch):
    class Dummy:
        def invoke(self, messages, **kw):
            assert kw["role"] == "claims"
            return ccm.AIMessage(content="[1]")
    t1, t2 = ask.CURRENT_MODEL.set(Dummy()), ask.ROLE.set("claims")
    try:
        assert ask.ask_json("x") == [1]
    finally:
        ask.CURRENT_MODEL.reset(t1)
        ask.ROLE.reset(t2)


def test_data_url_and_v0_blocks_are_images(tmp_path):
    msgs = [ccm.HumanMessage(content=[{"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="}},
                                      {"type": "image", "source_type": "base64", "data": "AAAA", "mime_type": "image/jpeg"},
                                      {"type": "text", "text": "q"}])]
    assert ccm.has_images(msgs)
    out = ccm.materialize_images(msgs, tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["img1.png", "img2.jpg"]
    assert "[image file img2.jpg]" in ccm._text(out[0].content)
