import json
import runpy
import sys

import ai_playground
import ai_sidecar


def test_ai_playground_main_forwards_cli_flags(monkeypatch, capsys):
    calls = []

    def fake_run_once(*, persist, write_signal):
        calls.append({"persist": persist, "write_signal": write_signal})
        return {"decisionId": "playground-1"}

    monkeypatch.setattr(ai_playground, "_run_once", fake_run_once)
    monkeypatch.setattr(sys, "argv", ["ai_playground.py", "--persist", "--write-signal"])

    ai_playground.main()

    assert calls == [{"persist": True, "write_signal": True}]
    assert json.loads(capsys.readouterr().out) == {"decisionId": "playground-1"}


def test_ai_playground_entrypoint_prints_one_review(monkeypatch, capsys):
    calls = []

    def fake_run_once(*, persist, write_signal):
        calls.append({"persist": persist, "write_signal": write_signal})
        return {"decisionId": "entrypoint-review"}

    monkeypatch.setattr(ai_sidecar, "_run_once", fake_run_once)
    monkeypatch.setattr(sys, "argv", ["ai_playground.py"])

    runpy.run_path(ai_playground.__file__, run_name="__main__")

    assert calls == [{"persist": False, "write_signal": False}]
    assert json.loads(capsys.readouterr().out) == {"decisionId": "entrypoint-review"}
