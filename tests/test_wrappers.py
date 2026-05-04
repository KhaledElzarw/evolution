import run_dashboard_detached
import run_engine_detached
import run_ai_sidecar_detached


def test_wrapper_python_executable_defaults_to_venv(monkeypatch):
    monkeypatch.delenv("TRADEBOT_PYTHON", raising=False)

    assert run_engine_detached.get_python_executable() == run_engine_detached.PYTHON
    assert run_dashboard_detached.get_python_executable() == run_dashboard_detached.PYTHON
    assert run_ai_sidecar_detached.get_python_executable() == run_ai_sidecar_detached.PYTHON


def test_wrapper_python_executable_honors_override(monkeypatch):
    monkeypatch.setenv("TRADEBOT_PYTHON", "/custom/python")

    assert run_engine_detached.get_python_executable() == "/custom/python"
    assert run_dashboard_detached.get_python_executable() == "/custom/python"
    assert run_ai_sidecar_detached.get_python_executable() == "/custom/python"


def test_engine_start_uses_configurable_python(monkeypatch, tmp_path):
    calls = []

    class Proc:
        pid = 321

    def fake_popen(args, **kwargs):
        calls.append((args, kwargs))
        return Proc()

    monkeypatch.setenv("TRADEBOT_PYTHON", "/custom/python")
    monkeypatch.setattr(run_engine_detached, "PID_PATH", tmp_path / "engine.pid")
    monkeypatch.setattr(run_engine_detached, "LOG_PATH", tmp_path / "engine.log")
    monkeypatch.setattr(run_engine_detached, "_all_engine_pids", lambda: [])
    monkeypatch.setattr(run_engine_detached, "_detached_engine_pids", lambda: [321])
    monkeypatch.setattr(run_engine_detached.subprocess, "Popen", fake_popen)

    assert run_engine_detached.start() == 321
    assert calls[0][0][0] == "/custom/python"


def test_dashboard_start_uses_configurable_python(monkeypatch, tmp_path):
    calls = []

    class Proc:
        pid = 654

    def fake_popen(args, **kwargs):
        calls.append((args, kwargs))
        return Proc()

    monkeypatch.setenv("TRADEBOT_PYTHON", "/custom/python")
    monkeypatch.setattr(run_dashboard_detached, "PID_PATH", tmp_path / "dashboard.pid")
    monkeypatch.setattr(run_dashboard_detached, "LOG_PATH", tmp_path / "dashboard.log")
    monkeypatch.setattr(run_dashboard_detached, "_listening_pid_on_port", lambda: None)
    monkeypatch.setattr(run_dashboard_detached, "_live_dashboard_pids", lambda: [])
    monkeypatch.setattr(run_dashboard_detached.subprocess, "Popen", fake_popen)

    assert run_dashboard_detached.start() == 654
    assert calls[0][0][0] == "/custom/python"


def test_ai_sidecar_start_uses_configurable_python(monkeypatch, tmp_path):
    calls = []

    class Proc:
        pid = 987

    def fake_popen(args, **kwargs):
        calls.append((args, kwargs))
        return Proc()

    monkeypatch.setenv("TRADEBOT_PYTHON", "/custom/python")
    monkeypatch.setattr(run_ai_sidecar_detached, "PID_PATH", tmp_path / "ai_sidecar.pid")
    monkeypatch.setattr(run_ai_sidecar_detached, "LOG_PATH", tmp_path / "ai_sidecar.log")
    monkeypatch.setattr(run_ai_sidecar_detached, "_live_pids", lambda: [])
    monkeypatch.setattr(run_ai_sidecar_detached.subprocess, "Popen", fake_popen)

    assert run_ai_sidecar_detached.start() == 987
    assert calls[0][0][0] == "/custom/python"


def test_engine_status_is_observational(monkeypatch, tmp_path):
    pid_path = tmp_path / "engine.pid"
    pid_path.write_text("1", encoding="utf-8")
    monkeypatch.setattr(run_engine_detached, "PID_PATH", pid_path)
    monkeypatch.setattr(run_engine_detached, "_all_engine_pids", lambda: [10, 20])

    assert run_engine_detached.status() == 20
    assert pid_path.read_text(encoding="utf-8") == "1"


def test_dashboard_status_is_observational(monkeypatch, tmp_path):
    pid_path = tmp_path / "dashboard.pid"
    pid_path.write_text("1", encoding="utf-8")
    monkeypatch.setattr(run_dashboard_detached, "PID_PATH", pid_path)
    monkeypatch.setattr(run_dashboard_detached, "_listening_pid_on_port", lambda: 99)
    monkeypatch.setattr(run_dashboard_detached, "_live_dashboard_pids", lambda: [10, 20])

    assert run_dashboard_detached.status() == 99
    assert pid_path.read_text(encoding="utf-8") == "1"


def test_ai_sidecar_status_is_observational(monkeypatch, tmp_path):
    pid_path = tmp_path / "ai_sidecar.pid"
    pid_path.write_text("1", encoding="utf-8")
    monkeypatch.setattr(run_ai_sidecar_detached, "PID_PATH", pid_path)
    monkeypatch.setattr(run_ai_sidecar_detached, "_live_pids", lambda: [30, 40])

    assert run_ai_sidecar_detached.status() == 40
    assert pid_path.read_text(encoding="utf-8") == "1"
